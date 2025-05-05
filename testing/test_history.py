import pytest
from datetime import datetime, UTC
from ssh_ops_history import CommandHistoryManager
from ssh_models import CommandHandle

def test_add_and_retrieve_command():
    print("\n--- test_add_and_retrieve_command ---")
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
    print("\n--- test_update_handle ---")
    manager = CommandHistoryManager()
    handle = manager.add_command("original")
    handle.cmd = "updated"
    manager.update_handle(handle)
    
    assert manager.get_handle(handle.id).cmd == "updated"

def test_history_order():
    print("\n--- test_history_order ---")
    manager = CommandHistoryManager(history_limit=3)
    handles = [manager.add_command(f"cmd{i}") for i in range(5)]
    
    history = manager.get_history()
    assert len(history) == 3
    assert history[0]['cmd'] == "cmd2"  # Oldest kept command
    assert history[-1]['cmd'] == "cmd4" # Newest command

def test_get_nonexistent_handle():
    print("\n--- test_get_nonexistent_handle ---")
    manager = CommandHistoryManager()
    with pytest.raises(KeyError):
        manager.get_handle(999)

def test_update_nonexistent_handle():
    print("\n--- test_update_nonexistent_handle ---")
    manager = CommandHistoryManager()
    fake_handle = CommandHandle(999, "fake")
    with pytest.raises(KeyError):
        manager.update_handle(fake_handle)

def test_output_retrieval():
    print("\n--- test_output_retrieval ---")
    manager = CommandHistoryManager()
    handle = manager.add_command("test")
    
    # Add some output lines
    for i in range(100):
        handle.add_output(f"Line {i}")
    
    # Test retrieving different amounts
    assert len(handle.tail(10)) == 10
    assert len(handle.tail(50)) == 50
    assert len(handle.tail(200)) == 100  # Only 100 available
    assert len(handle.tail(0)) == 0
    
    # Verify content
    last_10 = handle.tail(10)
    assert last_10[0] == "Line 90"
    assert last_10[-1] == "Line 99"

def test_output_truncation():
    print("\n--- test_output_truncation ---")
    manager = CommandHistoryManager(recent_full_output=0, default_tail=50)
    handle = manager.add_command("test")
    
    # Add more lines than we'll keep
    for i in range(100):
        handle.add_output(f"Line {i}")
    
    # Should only get last 50 lines
    output = handle.tail(100)
    assert len(output) == 50
    assert output[0] == "Line 50"
    assert output[-1] == "Line 99"

def test_empty_output():
    print("\n--- test_empty_output ---")
    manager = CommandHistoryManager()
    handle = manager.add_command("test")
    
    assert len(handle.tail(10)) == 0
    assert len(handle.tail(100)) == 0

def test_command_metadata():
    print("\n--- test_command_metadata ---")
    manager = CommandHistoryManager()
    handle = manager.add_command("test_command")
    
    # Add some output and mark as completed
    handle.add_output("output line 1")
    handle.add_output("output line 2")
    handle.running = False
    handle.exit_code = 0
    handle.end_ts = datetime.now(UTC)
    
    # Get metadata
    info = handle.info()
    
    # Verify basic fields
    assert info['id'] == handle.id
    assert info['cmd'] == "test_command"
    assert info['running'] is False
    assert info['exit_code'] == 0
    assert info['total_lines'] == 2
    assert info['truncated'] is False
    
    # Verify timestamps
    assert 'start_ts' in info
    assert 'end_ts' in info
    assert info['end_ts'].endswith('Z')
    assert info['start_ts'].endswith('Z')
    
    # Verify output metadata
    assert info['output_lines'] == 2
    assert info['tail_keep'] is None  # Default for recent commands
    
    # Verify history manager preserves metadata
    history = manager.get_history()
    assert len(history) == 1
    assert history[0]['cmd'] == "test_command"
    assert history[0]['exit_code'] == 0

def test_output_after_truncation():
    print("\n--- test_output_after_truncation ---")
    manager = CommandHistoryManager(recent_full_output=1, default_tail=50)
    
    # First command will be truncated
    handle1 = manager.add_command("test1")
    for i in range(100):
        handle1.add_output(f"Line {i}")
    
    # Second command keeps full output
    handle2 = manager.add_command("test2")
    for i in range(75):
        handle2.add_output(f"Line {i}")
    
    # Verify truncation behavior
    assert len(handle1.tail(100)) == 50  # Truncated to last 50
    assert len(handle2.tail(100)) == 75  # Full output available

if __name__ == "__main__":
    print("Running command history tests...")
    test_add_and_retrieve_command()
    test_update_handle()
    test_history_order()
    test_output_retrieval()
    test_output_truncation()
    test_empty_output()
    test_command_metadata()
    test_output_after_truncation()
    try:
        test_get_nonexistent_handle()
        print("test_get_nonexistent_handle passed")
    except Exception as e:
        print(f"test_get_nonexistent_handle failed: {e}")
    
    try:
        test_update_nonexistent_handle()
        print("test_update_nonexistent_handle passed")
    except Exception as e:
        print(f"test_update_nonexistent_handle failed: {e}")
    
    print("All command history tests completed.")
