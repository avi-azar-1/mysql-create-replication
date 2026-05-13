# MySQL Replication Manager

A CLI tool that automates MySQL replication setup by cloning data from a master
(source) server to a replica using the **MySQL Clone Plugin**, then configuring
and starting GTID-based replication — all in one command.

## Prerequisites

| Requirement | Details |
|---|---|
| **MySQL 8.0+** | Both master and replica |
| **Clone Plugin** | Installed on both servers (`INSTALL PLUGIN clone SONAME 'mysql_clone.so';`) |
| **GTID mode** | Enabled on both servers (`gtid_mode=ON`, `enforce_gtid_consistency=ON`) |
| **my.cnf** | Already configured for replication (`server-id`, `log_bin`, etc.) |
| **Network** | Replica can reach master on the MySQL port |

> **Note:** The tool will automatically create the clone and replication users on
> the master if they don't exist (using the credentials from `.env`). It will also
> remove the `component_validate_password` plugin if installed, to avoid password
> policy errors during user creation.

## Setup

```bash
# 1. Clone the repo
git clone <repo-url> && cd mysql-create-replication

# 2. Create a virtual environment (recommended)
python -m venv .venv && .venv\Scripts\activate   # Windows
# python3 -m venv .venv && source .venv/bin/activate  # Linux/macOS

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure credentials
copy .env.example .env
# Edit .env with your actual credentials
```

## Usage

```bash
python mysql_replication.py --master <host:port> --replica <host:port>
```

Servers are specified in `host:port` format. Port defaults to **3306** if omitted.

### Options

| Flag | Short | Description |
|---|---|---|
| `--master` | `-m` | Master (source) server as `host:port` |
| `--replica` | `-r` | Target replica server as `host:port` |
| `--admin-user` | `-u` | Privileged admin username (overrides `MYSQL_ROOT_USER` in `.env`) |
| `--admin-password` | `-p` | Privileged admin password (overrides `MYSQL_ROOT_PASSWORD` in `.env`) |
| `--help` | `-h` | Show help message |

> Both `--admin-user` and `--admin-password` must be provided together.
> If neither is specified, the tool falls back to the `.env` values.

### Examples

```bash
# Both servers on default port 3306, admin creds from .env
python mysql_replication.py -m db-master-01 -r db-replica-02

# Custom ports
python mysql_replication.py -m db-master-01:3307 -r db-replica-02:3308

# Override admin credentials via CLI
python mysql_replication.py -m db-master-01 -r db-replica-02 -u dba_admin -p secret123
```

## What It Does

```
Pre-flight — User Provisioning
  ├─ Remove component_validate_password (if installed)
  ├─ Ensure clone user exists on master (create + BACKUP_ADMIN if missing)
  └─ Ensure replication user exists on master (create + REPLICATION SLAVE,
     REPLICATION CLIENT if missing)

Phase 1 — Verify
  ├─ Display server topology (hostname, version, server-id, UUID, …)
  ├─ Check the replica for active connections & running queries
  └─ Prompt for confirmation (destructive operation warning)

Phase 2 — Reset & Clone
  ├─ STOP REPLICA  /  RESET REPLICA ALL
  ├─ Verify GTID is enabled on master
  ├─ CLONE INSTANCE FROM master
  └─ Wait for replica to restart after clone

Phase 3 — Configure & Start Replication
  ├─ CHANGE REPLICATION SOURCE TO … SOURCE_AUTO_POSITION = 1
  ├─ START REPLICA
  └─ Display replication health (IO/SQL threads, lag, GTID sets)
```

## Configuration

Credentials are read from a `.env` file in the project root.
The admin user can optionally be overridden via CLI flags.

| Variable | Purpose |
|---|---|
| `MYSQL_ROOT_USER` | Admin user for connecting to both servers (overridden by `--admin-user`) |
| `MYSQL_ROOT_PASSWORD` | Admin password (overridden by `--admin-password`) |
| `MYSQL_CLONE_USER` | Clone user — created automatically if missing |
| `MYSQL_CLONE_PASSWORD` | Clone user password |
| `MYSQL_REPL_USER` | Replication user — created automatically if missing |
| `MYSQL_REPL_PASSWORD` | Replication user password |

## License

MIT
