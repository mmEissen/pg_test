

import pytest


def test_plugin(testdir):
    testdir.makepyfile(
        """
        def test_database(pg_database):
            assert pg_database

        def test_database_pool(pg_database_pool):
            assert pg_database_pool
        """
    )
    result = testdir.runpytest()

    result.assert_outcomes(passed=2)


def test_plugin_max_pool_size(testdir):
    max_pool_size = 5

    testdir.makepyfile(
        f"""
        def test_database_pool(pg_database_pool):
            assert pg_database_pool.max_pool_size == {max_pool_size}
        """
    )

    result = testdir.runpytest("--max-pool-size", str(max_pool_size))

    result.assert_outcomes(passed=1)



def test_plugin_pg_setup_db_fixture(testdir):
    testdir.makepyfile(
        f"""
        import pytest
        import psycopg2

        def setup_db(pg_params):
            conn = psycopg2.connect(**pg_params.connection_kwargs())
            cursor = conn.cursor()
            cursor.execute("COMMIT")
            cursor.execute(
                "CREATE TABLE person ("
                "  user_id int,"
                "  name text"
                ")"
            )
            cursor.close()
            conn.close()

        @pytest.fixture(scope="session")
        def pg_setup_db():
            return setup_db

        def test_table_setup(pg_database):
            conn = psycopg2.connect(**pg_database.connection_kwargs())
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM person")
            result = cursor.fetchall()
        """
    )

    result = testdir.runpytest()

    result.assert_outcomes(passed=1)


@pytest.mark.skip()
def test_load(testdir):
    testdir.makepyfile(
        """
        import pytest
        import psycopg2

        TABLES = 100

        def setup_db(pg_params):
            conn = psycopg2.connect(**pg_params.connection_kwargs())
            cursor = conn.cursor()
            cursor.execute("COMMIT")
            for i in range(TABLES):
                cursor.execute(
                    f"CREATE TABLE person_{i} ("
                    "  user_id BIGSERIAL PRIMARY KEY,"
                    "  name TEXT"
                    ")"
                )
            cursor.close()
            conn.close()

        @pytest.fixture(scope="session")
        def pg_setup_db():
            return setup_db

        @pytest.mark.parametrize("number", range(100))
        def test_table_setup(pg_database, number):
            conn = psycopg2.connect(**pg_database.connection_kwargs())
            cursor = conn.cursor()
            for i in range(TABLES):
                cursor.execute(f"INSERT INTO person_{i}(name) VALUES ('name_{i}')")
            for i in range(TABLES):
                cursor.execute(f"SELECT * FROM person_{i}")
                cursor.fetchall()
        """
    )

    result = testdir.runpytest()

    result.assert_outcomes(passed=100)
