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

With pytest:
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

The plugin The following fixtures:

 - `pg_database`: `DatabaseParams` for a clean database.
 - `pg_database_pool`: A `DatabasePool` instance. Use this if you need more than one database in your tests at a time.


### Configuring Database Migrations

Use the below template in your `conftest.py` to configure how your databases are set up. 
```py
def setup_db(pg_params):
    """Add any setup logic for your database in here."""
    pass

@pytest.fixture(scope="session")
def pg_setup_db():
    return setup_db
```
Note: *You might be inclined to edit the above code to nest the setup_db function inside of the fixture function. This will not work, because the fixture result needs to be [pickleable](https://docs.python.org/3/library/pickle.html#what-can-be-pickled-and-unpickled)!*


### Advanced Usage (and other testing frameworks)

For other use cases you can use the `database_pool` context manager:
```py
with database_pool() as db_pool:
    with db_pool.database as db_params:
        connection = psycopg2.connect(**db_params.connection_kwargs())
```
