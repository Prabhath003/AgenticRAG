import pymysql
from typing import Any, Optional, Type, Dict, List
from types import TracebackType
from dataclasses import dataclass

from ...config import Config
from ...log_creator import get_file_logger

logger = get_file_logger()


@dataclass
class ColumnDef:
    """Column definition for schema management"""

    name: str
    type: str
    nullable: bool = True
    default: Optional[str] = None
    primary_key: bool = False
    unique: bool = False
    auto_increment: bool = False
    foreign_key: Optional[tuple[str, str]] = None

    def to_sql(self) -> str:
        """Convert to SQL column definition"""
        parts = [f"`{self.name}`", self.type]

        if self.primary_key:
            parts.append("PRIMARY KEY")
        if self.auto_increment:
            parts.append("AUTO_INCREMENT")
        if self.unique and not self.primary_key:
            parts.append("UNIQUE")
        if not self.nullable and not self.primary_key:
            parts.append("NOT NULL")
        if self.default is not None:
            parts.append(f"DEFAULT {self.default}")

        return " ".join(parts)


class DatabaseManager:
    """Manages database creation and schema evolution with auto-creation and union"""

    _instance: Optional["DatabaseManager"] = None
    _initialized: bool = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.host = Config.AWS_RDS_HOST_URL or "localhost"
        self.port = Config.AWS_RDS_PORT
        self.user = Config.AWS_RDS_USER
        self.password = Config.AWS_RDS_PASSWORD
        self.database = Config.DATABASE_NAME
        self.charset = Config.AWS_RDS_CHARSET
        self._initialized = True

        # Initialize database on first instantiation
        self.ensure_database_exists()

    def _get_connection(self, database: Optional[str] = None) -> Any:
        """Create a connection to MySQL"""
        try:
            conn = pymysql.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=database,
                charset=self.charset,
                cursorclass=pymysql.cursors.DictCursor,
                connect_timeout=5,
            )
            return conn
        except pymysql.err.OperationalError as e:
            logger.error(f"Database connection error: {e}")
            raise

    def ensure_database_exists(self) -> bool:
        """Create database if it doesn't exist"""
        try:
            # Connect without specifying a database
            conn = self._get_connection(database=None)
            cursor = conn.cursor()

            # Create database if it doesn't exist
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{self.database}` CHARACTER SET {self.charset}"
            )
            conn.commit()
            cursor.close()
            conn.close()

            logger.info(f"Database '{self.database}' ensured to exist")
            return True
        except Exception as e:
            logger.error(f"Failed to create database: {e}")
            raise

    def ensure_table_exists(
        self,
        table_name: str,
        columns: List[ColumnDef],
        indexes: Optional[Dict[str, List[str]]] = None,
    ) -> bool:
        """Create table if it doesn't exist"""
        try:
            if not self.table_exists(table_name):
                conn = self._get_connection(self.database)
                cursor = conn.cursor()

                # Build CREATE TABLE statement
                column_defs = [col.to_sql() for col in columns]
                create_sql = f"CREATE TABLE IF NOT EXISTS `{table_name}` ({', '.join(column_defs)}"

                # Add indexes
                if indexes:
                    for index_name, index_cols in indexes.items():
                        cols_str = ", ".join([f"`{col}`" for col in index_cols])
                        create_sql += f", INDEX `{index_name}` ({cols_str})"

                create_sql += ")"

                cursor.execute(create_sql)
                conn.commit()
                cursor.close()
                conn.close()

                logger.info(f"Table '{table_name}' created in database '{self.database}'")
            return True
        except Exception as e:
            logger.error(f"Failed to create table '{table_name}': {e}")
            return False

    def get_existing_columns(self, table_name: str) -> Dict[str, str]:
        """Get existing columns for a table"""
        try:
            conn = self._get_connection(self.database)
            cursor = conn.cursor()

            cursor.execute(f"DESCRIBE `{table_name}`")
            columns = cursor.fetchall()

            result: Dict[str, str] = {}
            if columns:
                for col in columns:
                    if isinstance(col, dict) and "Field" in col and "Type" in col:
                        result[col["Field"]] = col["Type"]

            cursor.close()
            conn.close()

            return result
        except pymysql.err.ProgrammingError:
            # Table doesn't exist
            return {}
        except Exception as e:
            logger.error(f"Failed to get columns for table '{table_name}': {e}")
            return {}

    def merge_columns(
        self,
        table_name: str,
        new_columns: List[ColumnDef],
        create_if_missing: bool = True,
    ) -> bool:
        """
        Merge new columns with existing ones (schema union).

        This function intelligently:
        1. Keeps existing columns
        2. Adds new columns that don't exist
        3. Handles conflicts gracefully
        """
        try:
            # Get existing columns
            existing_cols = self.get_existing_columns(table_name)

            if not existing_cols:
                # Table doesn't exist - create it
                if create_if_missing:
                    return self.ensure_table_exists(table_name, new_columns)
                return False

            conn = self._get_connection(self.database)
            cursor = conn.cursor()

            # Add new columns that don't exist
            for new_col in new_columns:
                col_name = new_col.name
                if col_name not in existing_cols:
                    try:
                        alter_sql = f"ALTER TABLE `{table_name}` ADD COLUMN {new_col.to_sql()}"
                        cursor.execute(alter_sql)
                        logger.info(f"Added column '{col_name}' to table '{table_name}'")
                    except pymysql.err.OperationalError as e:
                        logger.warning(f"Could not add column '{col_name}': {e}")

            conn.commit()
            cursor.close()
            conn.close()

            return True
        except Exception as e:
            logger.error(f"Failed to merge columns for table '{table_name}': {e}")
            return False

    def table_exists(self, table_name: str) -> bool:
        """Check if a table exists"""
        try:
            conn = self._get_connection(self.database)
            cursor = conn.cursor()

            cursor.execute(
                f"SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA='{self.database}' AND TABLE_NAME='{table_name}'"
            )
            result = cursor.fetchone()

            cursor.close()
            conn.close()

            return result is not None
        except Exception as e:
            logger.error(f"Failed to check if table '{table_name}' exists: {e}")
            return False

    def drop_table(self, table_name: str) -> bool:
        """Drop a table (for testing)"""
        try:
            conn = self._get_connection(self.database)
            cursor = conn.cursor()

            cursor.execute(f"DROP TABLE IF EXISTS `{table_name}`")
            conn.commit()
            cursor.close()
            conn.close()

            logger.info(f"Table '{table_name}' dropped")
            return True
        except Exception as e:
            logger.error(f"Failed to drop table '{table_name}': {e}")
            return False


# Global database manager instance
def get_db_manager() -> DatabaseManager:
    """Get or create the global database manager"""
    return DatabaseManager()


class DBSession:
    def __init__(self, database: Optional[str] = None):
        # Ensure database exists
        manager = get_db_manager()

        self.conn = pymysql.connect(
            database=database or manager.database,
            host=manager.host,
            port=manager.port,
            user=manager.user,
            password=manager.password,
            charset=manager.charset,
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=5,
        )
        self.cur = self.conn.cursor()
        self.db_manager = manager

    def execute(self, query: str, params: Any = None):
        self.cur.execute(query, params)
        return self.cur

    def fetchall(self):
        return self.cur.fetchall()

    def fetchone(self):
        return self.cur.fetchone()

    def ensure_table_exists(self, table_name: str, columns: List[ColumnDef]) -> bool:
        """Ensure a table exists with the given columns"""
        return self.db_manager.ensure_table_exists(table_name, columns)

    def merge_columns(self, table_name: str, columns: List[ColumnDef]) -> bool:
        """Merge columns into table (schema union)"""
        return self.db_manager.merge_columns(table_name, columns)

    def table_exists(self, table_name: str) -> bool:
        """Check if table exists"""
        return self.db_manager.table_exists(table_name)

    def __enter__(self):
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        if exc:
            self.conn.rollback()
        else:
            self.conn.commit()
        self.cur.close()
        self.conn.close()
