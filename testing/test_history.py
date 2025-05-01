import pytest
from ssh_history import CommandHistoryManager
from ssh_models import CommandHandle

def test_add_and_retrieve_command():
    manager = CommandHistoryManager(history_limit=2)
    handle1 = manager.add_command("cmd1")
    handle2 = manager.add_command("cmd2")
    
    assert manager.get_handle(handle1.id).cmd == "cmd1"
    assert manager.get_handle(handle2.id).cmd == "cmd2"
    
    # Test history trimming
    handle3 = manager.add_command("cmd3")
    with pytest.raises(KeyError):
        manager.get_handle(handle1.id)  # Should be trimmed
    
    assert len(manager.get_history()) == 2

def test_update_handle():
    manager = CommandHistoryManager()
    handle = manager.add_command("original")
    handle.cmd = "updated"
    manager.update_handle(handle)
    
    assert manager.get_handle(handle.id).cmd == "updated"

def test_history_order():
    manager = CommandHistoryManager(history_limit=3)
    handles = [manager.add_command(f"cmd{i}") for i in range(5)]
    
    history = manager.get_history()
    assert len(history) == 3
    assert history[0]['cmd'] == "cmd2"  # Oldest kept command
    assert history[-1]['cmd'] == "cmd4" # Newest command

def test_get_nonexistent_handle():
    manager = CommandHistoryManager()
    with pytest.raises(KeyError):
        manager.get_handle(999)

def test_update_nonexistent_handle():
    manager = CommandHistoryManager()
    fake_handle = CommandHandle(999, "fake")
    with pytest.raises(KeyError):
        manager.update_handle(fake_handle)
