# PG Docker

A python package to provide containerized postgres databases in python

**Why would you need this?**

If you are using postgres and want to write tests that run against a real database, then this will make your life easier.

## Installation

Install via pip:
```
pip install pg-docker
```

You will also need to have [docker](https://www.docker.com/).

## Usage

Note: *This package is mainly built with pytest in mind, but you can use the context managers documented below with other testing frameworks as well.*

### Example

```py
import psycopg2


pytest_plugins = ["pg_docker"]


def test_using_a_database(pg_database):
    db_connection = psycopg2.connect(**pg_database.connection_kwargs())
    cursor = db_connection.cursor()
    cursor.execute("SELECT 'hello world!'")

    assert cursor.fetchone() == ("hello world!",)
```

### Usage with pytest

You first need to enable the plugin. To do this add a `conftest.py` to the root directory of your tests and add:
```py
pytest_plugins = ["pg_docker"]
```
You can find more details on how to activate plugins in the [pytest docs](https://docs.pytest.org/en/latest/how-to/plugins.html#requiring-loading-plugins-in-a-test-module-or-conftest-file)

The plugin provides two fixtures which you can use in your tests: `pg_database_pool` and `pg_database`


