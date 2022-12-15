import pytest
from pg_test import database_pool


def pytest_addoption(parser):
    group = parser.getgroup("pg_test")
    group.addoption(
        "--postgres-image-tag",
        action="store",
        dest="postgres_image_tag",
        default="latest",
        help="The image tag for the postgres docker image",
    )
    group.addoption(
        "--max-pool-size",
        action="store",
        dest="max_pool_size",
        default="10",
        type=int,
        help="The maximum number of databases in the pool",
    )


@pytest.fixture(scope="session")
def pg_database_pool(request):
    postgres_image_tag = request.config.getoption("postgres_image_tag")
    max_pool_size = request.config.getoption("max_pool_size")

    with database_pool(
        postgres_image_tag=postgres_image_tag, max_pool_size=max_pool_size
    ) as db_pool:
        yield db_pool


@pytest.fixture()
def pg_database(pg_database_pool):
    with pg_database_pool.database() as database:
        yield database
