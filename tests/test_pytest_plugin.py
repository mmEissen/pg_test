

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

