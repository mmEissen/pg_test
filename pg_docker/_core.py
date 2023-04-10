from __future__ import annotations
import functools
import queue
from typing import Callable, Generator, TypedDict, cast

import subprocess
import multiprocessing
import queue
import psycopg2
import socket
import dataclasses
import contextlib

_SHORT_TIMEOUT = 0.1
_LONG_TIMEOUT = _SHORT_TIMEOUT * 10
_PG_PASSWORD = "password"
_PG_ROOT_DATABASE = "postgres"
_PG_ROOT_USER = "postgres"


class CleanupProcessTerminatedError(Exception):
    pass


def _noop_setup_db(db_params: DatabaseParams) -> None:
    return


class _DBShuttingDownError(Exception):
    pass


class DatabaseCleaner:
    def __init__(
        self,
        root_params: DatabaseParams,
        clean_dbs: multiprocessing.Queue[DatabaseParams],
        dirty_dbs: multiprocessing.Queue[DatabaseParams],
        setup_db: Callable[[DatabaseParams], None] = _noop_setup_db,
    ) -> None:
        self._root_params = root_params
        self._stop_event = multiprocessing.Event()
        self.clean_dbs = clean_dbs
        self.dirty_dbs = dirty_dbs
        self.setup_db = setup_db

    @functools.cached_property
    def _cursor(self) -> psycopg2.cursor:
        connection = psycopg2.connect(**self._root_params.connection_kwargs())
        cursor = connection.cursor()
        cursor.execute("COMMIT")
        return cursor
    
    def _execute_sql(self, sql: str) -> None:
        try:
            self._cursor.execute(sql)
        except psycopg2.OperationalError as e:
            raise _DBShuttingDownError() from e

    def create_db(self, db_params: DatabaseParams) -> None:
        self._execute_sql(
            f"""
            CREATE USER {db_params.user}
                PASSWORD '{db_params.password}'
            """
        )
        self._execute_sql(
            f"""
            CREATE DATABASE {db_params.dbname}
                OWNER = {db_params.user}
            """
        )

    def drop_db(self, db_params: DatabaseParams) -> None:
        self._execute_sql(
            f"""
            SELECT pg_terminate_backend(pg_stat_activity.pid)
            FROM pg_stat_activity
            WHERE pid <> pg_backend_pid() 
            AND pg_stat_activity.datname = '{db_params.dbname}'
            """
        )
        self._execute_sql(
            f"""
            DROP DATABASE IF EXISTS {db_params.dbname}
        """
        )
        self._execute_sql(
            f"""
            DROP USER IF EXISTS {db_params.user}
            """
        )

    def drop_all_connections(self) -> None:
        self._execute_sql(
            """
            SELECT pg_terminate_backend(pg_stat_activity.pid)
            FROM pg_stat_activity
            WHERE pid <> pg_backend_pid()
            """
        )

    def maybe_clean_a_dirty_db(self) -> None:
        try:
            database_to_clean = self.dirty_dbs.get(timeout=_SHORT_TIMEOUT)
        except (multiprocessing.TimeoutError, queue.Empty):
            return
        self.drop_db(database_to_clean)
        self.create_db(database_to_clean)
        self.setup_db(database_to_clean)
        self.clean_dbs.put(database_to_clean)

    def run_forever(
        self,
    ) -> None:
        while not self._stop_event.is_set():
            try:
                self.maybe_clean_a_dirty_db()
            except _DBShuttingDownError:
                break
        self.drop_all_connections()
        self._cursor.close()
        self._cursor.connection.close()

    def stop(self) -> None:
        self._stop_event.set()


@dataclasses.dataclass()
class DatabasePool:
    root_params: DatabaseParams
    max_pool_size: int
    cleaning_workers: int
    setup_db: Callable[[DatabaseParams], None] = _noop_setup_db

    def __post_init__(self) -> None:
        self._dirty_dbs: multiprocessing.Queue[DatabaseParams] = multiprocessing.Queue()
        self._clean_dbs: multiprocessing.Queue[DatabaseParams] = multiprocessing.Queue()
        self._cleaners = [
            DatabaseCleaner(
                self.root_params, self._clean_dbs, self._dirty_dbs, self.setup_db
            )
            for _ in range(self.cleaning_workers)
        ]

        self._pool_size = 0

        self._wait_until_ready()
        self._cleanup_processes = self._launch_cleanup_processes()
        self._saturate_pool()

    def _saturate_pool(self) -> None:
        while self._pool_size < self.max_pool_size:
            self._add_db_to_pool()

    def _add_db_to_pool(self) -> None:
        self._pool_size += 1
        new_database = dataclasses.replace(
            self.root_params,
            dbname=f"__test_db_{self._pool_size}",
            user=f"__test_user_{self._pool_size}",
        )
        self._dirty_dbs.put(new_database)

    def _launch_cleanup_processes(self) -> list[multiprocessing.Process]:
        processes = [
            multiprocessing.Process(
                target=cleaner.run_forever,
                name=f"test-database-cleaner-{i}",
                daemon=True,
            )
            for i, cleaner in enumerate(self._cleaners)
        ]
        for process in processes:
            process.start()
        return processes

    def _wait_until_ready(self) -> None:
        while True:
            try:
                psycopg2.connect(**self.root_params.connection_kwargs())
            except psycopg2.Error as e:
                pass
            else:
                return

    def _get_db_and_check_on_cleanup(self) -> DatabaseParams:
        while True:
            try:
                return self._clean_dbs.get(timeout=_SHORT_TIMEOUT)
            except (queue.Empty, multiprocessing.TimeoutError):
                for process in self._cleanup_processes:
                    if not process.is_alive():
                        raise CleanupProcessTerminatedError()

    @contextlib.contextmanager
    def database(self) -> Generator[DatabaseParams, None, None]:
        """Returns the connection parameters to a clean database."""

        database = self._get_db_and_check_on_cleanup()
        try:
            yield database
        finally:
            self._dirty_dbs.put(database)

    def stop(self):
        for cleaner in self._cleaners:
            cleaner.stop()


class DatabaseParamsDict(TypedDict):
    host: str
    port: int
    dbname: str
    user: str
    password: str


@dataclasses.dataclass(frozen=True)
class DatabaseParams:
    host: str
    "The hostname"

    port: int
    "The port"

    dbname: str
    "The database name"

    user: str
    "The user name for the owner of the database"

    password: str
    "The user's password"

    def connection_kwargs(self) -> DatabaseParamsDict:
        """The connection parameters as a dictionary.
        
        This is compatible with the keyword arguments of `psycopg2.connect`:
        ```
        psycopg2.connect(**database_params.connection_kwargs())
        ```
        """
        return cast(DatabaseParamsDict, dataclasses.asdict(self))


def get_free_port() -> int:
    sock = socket.socket()
    sock.bind(("", 0))
    return sock.getsockname()[1]


@contextlib.contextmanager
def database_pool(
    *,
    postgres_image_tag: str = "latest",
    max_pool_size: int = 10,
    cleaning_workers: int = 5,
    docker_command: str = "docker",
    setup_db: Callable[[DatabaseParams], None] = _noop_setup_db,
) -> Generator[DatabasePool, None, None]:
    """A context manager to create a database pool.

    This will return an instance of `DatabasePool`.

    Keyword Arguments:
    postgres_image_tag: The docker image tag to use for the postgres container (default "latest")
    max_pool_size: The maximum number of databases to keep ready in the pool (default: 5)
    docker_command: The system command to run docker (default: "docker")
    setup_db: A callable to setup your databases. This will be run in the background after 
        commissioning each new database. The first argument to this callable is an instance of
        `DatabaseParams`. The callable passed to `setup_db` has to be pickleable. See 
        https://docs.python.org/3/library/pickle.html#what-can-be-pickled-and-unpickled for
        more info. 
    """
    port = get_free_port()
    docker_process = subprocess.Popen(
        [
            docker_command,
            "run",
            "--tmpfs",
            "/var/lib/postgresql/data"
            "--rm",
            "-t",
            f"-p{port}:5432",
            f"-ePOSTGRES_PASSWORD={_PG_PASSWORD}",
            f"postgres:{postgres_image_tag}",
            "-c", "fsync=off",
            "-c", "synchronous_commit=off",
            "-c", "full_page_writes=off",
        ],
        stderr=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
    )
    pool = DatabasePool(
        DatabaseParams(
            host="0.0.0.0",
            port=port,
            dbname=_PG_ROOT_DATABASE,
            user=_PG_ROOT_USER,
            password=_PG_PASSWORD,
        ),
        max_pool_size=max_pool_size,
        cleaning_workers=cleaning_workers,
        setup_db=setup_db
    )
    try:
        yield pool
    finally:
        pool.stop()
        docker_process.terminate()
        docker_process.wait(timeout=_LONG_TIMEOUT)
