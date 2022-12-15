from __future__ import annotations
import functools
import queue
from typing import Generator

import subprocess
import multiprocessing
import psycopg2
import socket
import dataclasses
import contextlib

_SHORT_TIMEOUT = 0.1
_LONG_TIMEOUT = _SHORT_TIMEOUT * 10


class DatabaseCleaner:
    def __init__(
        self, 
        root_params: DatabaseParams, 
        clean_dbs: multiprocessing.Queue[DatabaseParams],
        dirty_dbs: multiprocessing.Queue[DatabaseParams],
    ) -> None:
        self._root_params = root_params
        self._stop_event = multiprocessing.Event()
        self.clean_dbs = clean_dbs
        self.dirty_dbs = dirty_dbs

    @functools.cached_property
    def _cursor(self) -> psycopg2.cursor:
        connection = psycopg2.connect(**self._root_params.connection_kwargs())
        cursor = connection.cursor()
        cursor.execute("COMMIT")
        return cursor

    def create_db(self, db_params: DatabaseParams) -> None:
        self._cursor.execute(f"""
            CREATE USER {db_params.user}
                PASSWORD '{db_params.password}'
        """)
        self._cursor.execute(f"""
            CREATE DATABASE {db_params.dbname}
                OWNER = {db_params.user}
        """)
    
    def drop_db(self, db_params: DatabaseParams) -> None:
        self._cursor.execute(f"""
            SELECT pg_terminate_backend(pg_stat_activity.pid)
            FROM pg_stat_activity
            WHERE pid <> pg_backend_pid() 
            AND pg_stat_activity.datname = '{db_params.dbname}'
        """)
        self._cursor.execute(f"""
            DROP DATABASE IF EXISTS {db_params.dbname}
        """)
        self._cursor.execute(f"""
            DROP USER IF EXISTS {db_params.user}
        """)
    
    def drop_all_connections(self) -> None:
        self._cursor.execute("""
            SELECT pg_terminate_backend(pg_stat_activity.pid)
            FROM pg_stat_activity
            WHERE pid <> pg_backend_pid()
        """)

    def maybe_clean_a_dirty_db(self) -> None:
        try:
            database_to_clean = self.dirty_dbs.get(timeout=_SHORT_TIMEOUT)
        except multiprocessing.TimeoutError:
            return
        self.drop_db(database_to_clean)
        self.create_db(database_to_clean)
        self.clean_dbs.put(database_to_clean)

    def run_forever(
        self,
    ) -> None:
        while not self._stop_event.is_set():
            self.maybe_clean_a_dirty_db()
        self.drop_all_connections()
        self._cursor.close()
        self._cursor.connection.close()
    
    def stop(self) -> None:
        self._stop_event.set()


@dataclasses.dataclass()
class DatabasePool:
    root_params: DatabaseParams
    max_pool_size: int

    def __post_init__(self) -> None:
        self._dirty_dbs: multiprocessing.Queue[DatabaseParams] = multiprocessing.Queue()
        self._clean_dbs: multiprocessing.Queue[DatabaseParams] = multiprocessing.Queue()
        self._cleaner = DatabaseCleaner(self.root_params, self._clean_dbs, self._dirty_dbs)
        
        self._pool_size = 0

        self._wait_until_ready()
        self._cleanup_process = self._launch_cleanup_process()
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

    def _launch_cleanup_process(self) -> multiprocessing.Process:
        process = multiprocessing.Process(
            target=self._cleaner.run_forever, 
            name="test-database-cleanup",
            daemon=True,
        )
        process.start()
        return process

    def _wait_until_ready(self) -> None:
        while True:
            try:
                psycopg2.connect(**self.root_params.connection_kwargs())
            except psycopg2.Error as e:
                pass
            else:
                return

    @contextlib.contextmanager
    def database(self) -> Generator[DatabaseParams, None, None]:
        try:
            database = self._clean_dbs.get(timeout=_SHORT_TIMEOUT)
        except queue.Empty:
            # TODO maybe increase pool size? Or add cleanup workers?
            database = self._clean_dbs.get()
        try:
            yield database
        finally:
            self._dirty_dbs.put(database)
        
    def stop(self):
        self._cleaner.stop()


@dataclasses.dataclass(frozen=True)
class DatabaseParams:
    host: str
    port: int
    dbname: str
    user: str
    password: str

    def connection_kwargs(self) -> dict[str, object]:
        return dataclasses.asdict(self)


def get_free_port() -> int:
    sock = socket.socket()
    sock.bind(('', 0))
    return sock.getsockname()[1]


@contextlib.contextmanager
def database_pool(*, postgres_image_tag: str = "latest", max_pool_size: int = 5) -> Generator[DatabasePool, None, None]:
    port = get_free_port()
    docker_process = subprocess.Popen(
        [
            "docker",
            "run",
            "--rm",
            "-t",
            f"-p{port}:5432",
            "-ePOSTGRES_PASSWORD=password",
            f"postgres:{postgres_image_tag}",
        ],
    )
    pool = DatabasePool(
        DatabaseParams(
            host="0.0.0.0",
            port=port,
            dbname="postgres",
            user="postgres",
            password="password",
        ),
        max_pool_size=max_pool_size
    )
    try:
        yield pool
    finally:
        pool.stop()
        docker_process.terminate()
        docker_process.wait(timeout=_LONG_TIMEOUT)
