from app.policy.workspaces import WorkspaceRegistry
from app.tools.commands import CommandPolicy, CommandRisk, CommandRunner


def runner(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    return CommandRunner(WorkspaceRegistry({"test": root})), root


def test_safe_command_and_output(tmp_path):
    command, _ = runner(tmp_path)
    result = command.run_command("test", "python", ["--version"])
    assert result["status"] == "completed"
    assert result["exit_code"] == 0
    assert result["stdout"] or result["stderr"]


def test_policy_rejects_shell_network_and_git_mutation():
    assert CommandPolicy.classify("cmd.exe", ["/c", "dir"]) == CommandRisk.DENIED
    assert CommandPolicy.classify("git", ["push"]) == CommandRisk.DENIED
    assert CommandPolicy.classify("pip", ["install", "x"]) == CommandRisk.DENIED
    assert CommandPolicy.classify("git", ["status"]) == CommandRisk.READ_ONLY


def test_cwd_escape_and_missing_executable(tmp_path):
    command, _ = runner(tmp_path)
    assert command.run_command("test", "python", ["--version"], "..\\")["status"] == "rejected"
    assert command.run_command("test", "definitely-not-an-executable")["status"] == "denied"


def test_output_is_bounded_by_policy():
    assert CommandPolicy.classify("python", ["-c", "print('x')"]) == CommandRisk.DENIED
