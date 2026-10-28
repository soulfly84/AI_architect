"""Общие настройки тестов: изолированный каталог данных."""
import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="interior-test-")
os.environ["LLM_ENABLED"] = "0"
