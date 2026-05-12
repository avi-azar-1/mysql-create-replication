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

### Required MySQL Users

Create these users on **both** servers (or at least as noted):

```sql
-- Root / admin user (both servers)
-- (usually already exists)

-- Clone user (on the master — the donor)
CREATE USER 'clone_user'@'%' IDENTIFIED BY 'your_clone_password';
GRANT BACKUP_ADMIN ON *.* TO 'clone_user'@'%';

-- Replication user (on the master)
CREATE USER 'repl_user'@'%' IDENTIFIED BY 'your_repl_password';
GRANT REPLICATION SLAVE ON *.* TO 'repl_user'@'%';
```

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
| `--help` | `-h` | Show help message |

### Examples

```bash
# Both servers on default port 3306
python mysql_replication.py -m db-master-01 -r db-replica-02

# Custom ports
python mysql_replication.py -m db-master-01:3307 -r db-replica-02:3308
```

## What It Does

```
Phase 1 — Verify
  ├─ Connect to both servers
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

All credentials are read from a `.env` file in the project root:

| Variable | Purpose |
|---|---|
| `MYSQL_ROOT_USER` | Admin user for connecting to both servers |
| `MYSQL_ROOT_PASSWORD` | Admin password |
| `MYSQL_CLONE_USER` | User for clone plugin authentication |
| `MYSQL_CLONE_PASSWORD` | Clone user password |
| `MYSQL_REPL_USER` | Replication user configured in `CHANGE REPLICATION SOURCE` |
| `MYSQL_REPL_PASSWORD` | Replication user password |

## License

MIT
