CREATE DATABASE IF NOT EXISTS manager
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'admin'@'%' IDENTIFIED BY 'admin123';

GRANT ALL PRIVILEGES ON manager.* TO 'admin'@'%';
GRANT CREATE ON *.* TO 'admin'@'%';

FLUSH PRIVILEGES;

USE manager;

SOURCE /opt/text2sql/sql/runtime_store.sql;
