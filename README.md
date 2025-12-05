# Despliegue IoT on‑prem con Ansible

Playbook reproducible para levantar en Ubuntu 22.04 (VM/Proxmox) un stack on‑prem para telemetría de drones:

- **ThingsBoard CE** + PostgreSQL: recepción HTTP/MQTT y visualización.
- **Apache Kafka (KRaft)**: enrutamiento de eventos (`tb-telemetry`).
- **ClickHouse**: consumo desde Kafka mediante `Kafka` engine + `Materialized View` para almacenar series temporales.
- **Simulador de drones** (Python): envía telemetría cada 15s a ThingsBoard.
- **Grafana**: dashboard preprovisionado con datasource ClickHouse y alertas básicas.

## Estructura
- `ansible/playbooks/site.yml`: playbook principal.
- `ansible/inventories/local/hosts.ini`: inventario de ejemplo.
- `ansible/inventories/local/group_vars/iot_nodes.yml`: variables (puertos, tokens, imágenes).
- Roles: `docker`, `kafka`, `clickhouse`, `thingsboard`, `grafana`.
- `simulator/`: script de simulación y dependencias.
- Puerto Kafka para clientes externos: `29092` (interno entre contenedores: `kafka:9092`).
- Grafana: `http://<host>:3000` (admin/admin por defecto), con datasource ClickHouse y alertas provisionadas.

## Requisitos previos
- Ubuntu 22.04 target con acceso sudo.
- Python 3 y Ansible en el equipo de orquestación (`pip install ansible`).
- Red sin bloqueo a puertos 8080 (HTTP TB), 1883 (MQTT), 9092 (Kafka), 8123 (CH).

## Cómo ejecutar
```bash
cd ansible
# (opcional) activar entorno virtual
ansible-playbook -i inventories/local/hosts.ini playbooks/site.yml
```

Variables clave (`group_vars/iot_nodes.yml`) que puedes ajustar:
- `thingsboard_http_port` / `thingsboard_mqtt_port`
- `kafka_topic`
- `clickhouse_user` / `clickhouse_password`
- `thingsboard_devices`: nombres y tokens pre-provisionados para el simulador.
- Para correr en macOS sin sudo, pasa rutas de trabajo en tu `$HOME` y desactiva `become`:  
  `ansible-playbook -i inventories/local/hosts.ini playbooks/site.yml -e "ansible_become=false thingsboard_dir=$HOME/iot-stack/thingsboard clickhouse_dir=$HOME/iot-stack/clickhouse kafka_dir=$HOME/iot-stack/kafka kafka_data_dir=$HOME/iot-stack/kafka/data"`
  Cambia `grafana_port` y credenciales (`grafana_admin_user` / `grafana_admin_password`) si lo requieres.

## Credenciales por defecto
- ThingsBoard: usuario `tenant@thingsboard.org` / contraseña `tenant`.
- Grafana: usuario `admin` / contraseña `admin`.
- ClickHouse: usuario `telemetry` / contraseña `telemetry`.

El playbook instala Docker, levanta los contenedores y:
- Crea el tópico Kafka `tb-telemetry`.
- Configura ClickHouse con tabla `Kafka` + vista materializada hacia `telemetry`.
- Crea dispositivos en ThingsBoard con los tokens definidos.
- **Nota sobre TB → Kafka:** ThingsBoard CE no expone el nodo “Kafka” vía API; la receta deja el rule chain por defecto y el simulador publica en ThingsBoard (para UI) y en Kafka (para ingesta/ClickHouse). Si usas TB PE, añade manualmente un nodo Kafka en el root rule chain y apunta a `kafka:9092`.

## Verificación rápida
- `docker ps` debe mostrar `thingsboard`, `tb-db`, `kafka`, `clickhouse`.
- ThingsBoard UI: `http://<host>:8080` (usuario `tenant@thingsboard.org` / `tenant` por defecto).
- ClickHouse: `docker exec clickhouse clickhouse-client --query "SELECT * FROM telemetry.telemetry LIMIT 10"`.
- Kafka topic: `docker exec kafka kafka-topics.sh --list --bootstrap-server localhost:9092`.
- Grafana: `http://<host>:3000` (admin/admin) con datasource ClickHouse (plugin vertamedia) y dashboard `Drone Telemetry` ya cargado.
  - Alertas incluidas: batería promedio <20% en últimos 5m y “sin telemetría” en 5m.

## Simulador de drones
```bash
cd simulator
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python drone_simulator.py --url http://<host>:8080 --kafka-bootstrap localhost:29092 --interval 15 --verbose
```
- Usa los tokens declarados en `thingsboard_devices` (por defecto `drone-1-token`, etc.).
- Telemetría enviada: `drone_id`, `latitude`, `longitude`, `battery`, `altitude`, `speed` (timestamp en ms).
- El simulador publica en ThingsBoard (UI) y en Kafka (`tb-telemetry`), desde donde ClickHouse consume.

## Arquitectura (resumen)
- **Drones/Sim** → HTTP/MQTT → **ThingsBoard CE**
- **Simulador** → Kafka (`tb-telemetry`)
- **Kafka** → **ClickHouse Kafka engine** → **Materialized View** → tabla `telemetry`.
- **Grafana** → datasource ClickHouse + dashboard de batería y tabla de eventos.
- **Alerting** en Grafana (batería baja y falta de ingestión) como punto de partida.

## Decisiones técnicas
- **Docker Compose** para encapsular dependencias y facilitar limpieza/repetibilidad.
- **Kafka en KRaft** (sin Zookeeper) para minimizar nodos.
- **ClickHouse Kafka engine** + MV para ingestión continua sin servicios adicionales.
- **Bootstrap automático** de dispositivos ThingsBoard y fix de tokens para el simulador.
- **Grafana** provisionada (datasource + dashboard) para observabilidad rápida.
- Tokens de dispositivo fijos para que el simulador funcione sin pasos previos.

## Notas y próximos pasos
- Credenciales y tokens están en texto plano para simplicidad; ajústalos antes de producción.
- Añadir TLS y auth en Kafka/ClickHouse es recomendable para entornos reales.
- Bonus sugerido: agregar Grafana/Prometheus para métricas y dashboards sobre ClickHouse/Kafka.
- Para TB PE, añadir el nodo Kafka en el rule chain y eliminar el doble envío desde el simulador.
