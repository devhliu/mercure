import json

from common.constants import mercure_names
from dispatch.retry import increase_retry

dummy_info = {
    "action": "route",
    "uid": "",
    "uid_type": "series",
    "triggered_rules": "",
    "mrn": "",
    "acc": "",
    "sender_address": "localhost",
    "mercure_version": "",
    "mercure_appliance": "",
    "mercure_server": "",
}


def test_execute_increase(fs, mocker):
    source = "/var/data"
    fs.create_dir(source)

    target = {
        "id": "task_id",
        "info": dummy_info,
        "dispatch": {"target_name": "test_target"},
    }
    fs.create_file("/var/data/" + mercure_names.TASKFILE, contents=json.dumps(target))
    result = increase_retry(source, 5, 50)

    with open("/var/data/" + mercure_names.TASKFILE, "r") as f:
        modified_target = json.load(f)

    assert modified_target["dispatch"]["retries"] == 1
    assert modified_target["dispatch"]["next_retry_at"]
    assert result


def test_execute_increase_fail(fs, mocker):
    source = "/var/data"
    fs.create_dir(source)
    target = {
        "id": "task_id",
        "info": dummy_info,
        "dispatch": {
            "target_name": "test_target",
            "retries": 5,
        },
    }
    fs.create_file("/var/data/" + mercure_names.TASKFILE, contents=json.dumps(target))
    result = increase_retry(source, 5, 50)

    assert not result
