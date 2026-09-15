from app.config import config


def test_runtime_config_lock_is_context_manager():
    with config.runtime_config_lock():
        assert True


def test_try_acquire_runtime_config_lock_is_context_manager():
    with config.try_acquire_runtime_config_lock() as acquired:
        assert acquired is True
