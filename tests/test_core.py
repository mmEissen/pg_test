from __future__ import annotations

import dataclasses
import multiprocessing
import socket
import subprocess
from typing import Iterable
from unittest import mock

import pg_test
from pg_test import _core as core

import psycopg2
import pytest


@pytest.fixture
def postgres_image_tag() -> str:
    return "15"


def test_get_free_port() -> None:
    sock = socket.socket()

    port = core.get_free_port()

    sock.bind(("", port))


class TestDatabaseCleaner:
    @pytest.fixture
    def root_params(self, postgres_image_tag: str) -> Iterable[core.DatabaseParams]:
        port = core.get_free_port()
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
        params = core.DatabaseParams(
            host="0.0.0.0",
            port=port,
            dbname="postgres",
            user="postgres",
            password="password",
        )
        while True:
            try:
                psycopg2.connect(**params.connection_kwargs())
            except psycopg2.Error as e:
                pass
            else:
                break
        try:
            yield params
        finally:
            docker_process.terminate()

    @pytest.fixture
    def root_cursor(self, root_params: core.DatabaseParams):
        connection = psycopg2.connect(**root_params.connection_kwargs())
        cursor = connection.cursor()
        try:
            yield cursor
        finally:
            cursor.close()
            connection.close()

    @pytest.fixture
    def database_cleaner(
        self, root_params: core.DatabaseParams
    ) -> core.DatabaseCleaner:
        return core.DatabaseCleaner(
            root_params,
            mock.Mock(spec=multiprocessing.Queue()),
            mock.Mock(spec=multiprocessing.Queue()),
        )

    @pytest.fixture
    def some_database_params(
        self, root_params: core.DatabaseParams
    ) -> core.DatabaseParams:
        return dataclasses.replace(
            root_params,
            dbname=f"some_db_name",
            user=f"some_user_name",
        )

    def test_drop_db_works_if_db_does_not_exists(
        self,
        database_cleaner: core.DatabaseCleaner,
        some_database_params: core.DatabaseParams,
    ) -> None:
        database_cleaner.drop_db(some_database_params)

    def test_create_db_works_if_db_does_not_exists(
        self,
        database_cleaner: core.DatabaseCleaner,
        some_database_params: core.DatabaseParams,
        root_cursor: psycopg2.cursor,
    ) -> None:
        database_cleaner.create_db(some_database_params)

        root_cursor.execute("SELECT datname FROM pg_database")
        all_db_names = root_cursor.fetchall()
        assert (some_database_params.dbname,) in all_db_names

        root_cursor.execute("SELECT usename FROM pg_catalog.pg_user")
        all_user_names = root_cursor.fetchall()
        assert (some_database_params.user,) in all_user_names
    
    def test_drop_db_drops_db(
        self,
        database_cleaner: core.DatabaseCleaner,
        some_database_params: core.DatabaseParams,
        root_cursor: psycopg2.cursor,
    ) -> None:
        database_cleaner.create_db(some_database_params)

        database_cleaner.drop_db(some_database_params)

        root_cursor.execute("SELECT datname FROM pg_database")
        all_db_names = root_cursor.fetchall()
        assert (some_database_params.dbname,) not in all_db_names

        root_cursor.execute("SELECT usename FROM pg_catalog.pg_user")
        all_user_names = root_cursor.fetchall()
        assert (some_database_params.user,) not in all_user_names

    def test_drop_all_connections(
        self,
        database_cleaner: core.DatabaseCleaner,
        root_cursor: psycopg2.cursor,
    ) -> None:
        database_cleaner.drop_all_connections()

        with pytest.raises(psycopg2.OperationalError):
            root_cursor.execute("SELECT 1")

    def test_maybe_clean_a_dirty_db(
        self,
        database_cleaner: core.DatabaseCleaner,
        some_database_params: core.DatabaseParams,
        root_cursor: psycopg2.cursor,
    ) -> None:
        root_cursor.execute(
            "SELECT oid FROM pg_database WHERE datname = %s", (some_database_params.dbname,)
        )
        original_oid = root_cursor.fetchone()
        database_cleaner.create_db(some_database_params)
        database_cleaner.dirty_dbs.get.return_value = some_database_params

        database_cleaner.maybe_clean_a_dirty_db()

        database_cleaner.clean_dbs.put.assert_called_with(some_database_params)
        root_cursor.execute(
            "SELECT oid FROM pg_database WHERE datname = %s", (some_database_params.dbname,)
        )
        oid = root_cursor.fetchone()
        assert oid != original_oid


def test_connect_to_a_database(postgres_image_tag: str) -> None:
    with pg_test.database_pool(
        postgres_image_tag=postgres_image_tag
    ) as db_pool, db_pool.database() as database:
        psycopg2.connect(**database.connection_kwargs())


def test_connect_to_many_databases_subsequently(postgres_image_tag: str) -> None:
    with pg_test.database_pool(
        postgres_image_tag=postgres_image_tag,
        max_pool_size=4,
    ) as db_pool:
        for _ in range(20):
            with db_pool.database() as database:
                psycopg2.connect(**database.connection_kwargs())
