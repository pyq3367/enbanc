"""发布门禁回归：所有写入与Git操作仅发生在临时目录，无网络。"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import subprocess
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("enbanc_publish_tests", ROOT / ".sync" / "publish.py")
PUB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PUB)


def put(root, name, text="公开测试文本\n"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def capture(fn, *args):
    stream = io.StringIO()
    with redirect_stdout(stream), redirect_stderr(stream):
        rc = fn(*args)
    return rc, stream.getvalue()


def temp_git(root, *args):
    # 不读取真实身份和配置；只给临时仓库命令显式身份。
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT="0", GIT_AUTHOR_NAME="enbanc-fixture", GIT_COMMITTER_NAME="enbanc-fixture",
               GIT_AUTHOR_EMAIL="fixture@example.com", GIT_COMMITTER_EMAIL="fixture@example.com")
    r = subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=root,
                       capture_output=True, timeout=30, env=env)
    assert r.returncode == 0, "临时Git操作失败（输出隐藏）"
    return r.stdout


def test_privacy_public_paths_and_self_scan(tmp_path):
    names = ["README", "README.md", "README.zh-CN.md", "LICENSE", "CHANGELOG.md",
             "pyproject.toml", ".gitignore", ".sync/publish.py", "SKILL.md",
             "src/enbanc/a.py", "src/enbanc/providers/a.py", "tests/a.py",
             "docs/a.md", "docs/reference/a.md", "examples/demo/run.py", "examples/safety-rules.md"]
    for name in names:
        assert PUB.publishable(name)
        put(tmp_path, name)
    put(tmp_path, ".sync/publish.py", (ROOT / ".sync" / "publish.py").read_text(encoding="utf-8"))
    assert PUB.scan(tmp_path) == []
    payload = "sk" + "-" + "Q" * 24
    put(tmp_path, ".sync/publish.py", "# " + payload)
    findings = PUB.scan(tmp_path)
    assert any("API密钥" in line for line in findings)
    assert all(payload not in line for line in findings)


def test_privacy_patterns_and_no_match_output(tmp_path):
    fixtures = [
        ("IPv4地址", ".".join(["203", "0", "113", "9"])), 
        ("IPv6地址", ":".join(["fd12", "abcd", "0", "0", "0", "0", "0", "1"])),
        ("IPv6地址", "2001" + ":" + "db8" + "::" + "1"),
        ("IPv6地址", ":" * 2),
        ("IPv6地址", ":" * 2 + "1"),
        ("IPv6地址", "::" + "ffff" + ":" + ".".join(["192", "168", "1", "2"])),
        ("Windows绝对路径", "D" + ":" + "/" + "Private/person"),
        ("Windows绝对路径", "C" + ":" + chr(92) + "Users" + chr(92) + "fixture"),
        ("Windows绝对路径", chr(92) * 2 + "server" + chr(92) + "share"),
        ("用户目录路径", "/" + "home" + "/" + "fixture"),
        ("用户目录路径", "/" + "Users" + "/" + "fixture"),
        ("非占位凭据赋值", "api" + "_key" + " = " + repr("fixture" + "-credential")),
        ("非占位凭据赋值", "pass" + "word" + ": " + repr("fixture" + "-credential")),
        ("Bearer凭据", "Bear" + "er " + "R" * 24),
        ("API密钥", "sk" + "-" + "R" * 24),
        ("私钥", "-----" + "BEGIN " + "PRIVATE KEY" + "-----"),
        ("私有域名", "gateway" + "." + "internal"),
        ("私有模型标识", "fixture" + "@" + "intl"),
    ]
    for category, payload in fixtures:
        put(tmp_path, "README.md", payload)
        result = PUB.scan(tmp_path)
        assert any(category in line for line in result), "未检测到隐私类别"
        assert all(payload not in line for line in result), "诊断包含测试凭据"
        assert all(line.startswith("README.md:1  ") for line in result)


def test_privacy_placeholder_and_rule_syntax(tmp_path):
    text = "\n".join("api" + "_key = " + repr(value) for value in
                     ["", "<api-key>", "{model}", "your_api_key", "CHANGEME", "example", "redacted"])
    put(tmp_path, "tests/a.py", text)
    assert PUB.scan(tmp_path) == []


def test_privacy_unknown_paths_binary_and_suffixless(tmp_path):
    payload = "Bear" + "er " + "X" * 24
    put(tmp_path, "extra", payload)
    result = PUB.scan(tmp_path)
    assert any("白名单" in line for line in result)
    assert any("Bearer凭据" in line for line in result)
    assert all(payload not in line for line in result)
    put(tmp_path, "README.bin").write_bytes(bytes([255, 0, 128]))
    assert any("二进制" in line for line in PUB.scan(tmp_path))
    put(tmp_path, "README.nul").write_bytes(b"a\x00b")
    assert any("未知二进制" in line for line in PUB.scan(tmp_path))


def test_privacy_forbidden_paths(tmp_path):
    for i, name in enumerate([".env", ".env.production", ".sync/private.txt", "models.json",
                              "docs/private.log", "src/other/a.py", "tests/nested/a.py"]):
        root = tmp_path / str(i)
        root.mkdir()
        put(root, name)
        assert PUB.scan(root), "禁止路径意外通过"
    put(tmp_path, "runs/a.json", "本地运行内容")
    put(tmp_path, ".sync/state.json", "本地同步内容")
    assert not PUB.publishable("runs/a.json")
    assert not PUB.publishable(".sync/state.json")


def test_privacy_git_ignores_are_explicit(tmp_path):
    temp_git(tmp_path, "init", "-q")
    put(tmp_path, ".gitignore", "private.txt\n.env*\n.sync/state.json\n")
    put(tmp_path, "private.txt", "sk" + "-" + "Z" * 24)
    put(tmp_path, ".env", "私有夹具")
    put(tmp_path, "README.md")
    assert PUB.scan(tmp_path) == []
    put(tmp_path, "unknown.txt")
    assert any("白名单" in line for line in PUB.scan(tmp_path))


def test_privacy_tracked_ignored_and_staged_blobs(tmp_path):
    temp_git(tmp_path, "init", "-q")
    put(tmp_path, ".gitignore", "private.txt\nruns/\n.sync/state.json\n")
    put(tmp_path, "private.txt")
    put(tmp_path, "runs/a.json")
    put(tmp_path, ".sync/state.json")
    temp_git(tmp_path, "add", "-f", "private.txt", "runs/a.json", ".sync/state.json")
    result = PUB.scan(tmp_path)
    assert sum("Git已跟踪禁止路径" in line for line in result) == 3
    payload = "sk" + "-" + "S" * 24
    put(tmp_path, "README.md", payload)
    temp_git(tmp_path, "add", "README.md")
    put(tmp_path, "README.md")
    assert any("API密钥" in line for line in PUB.scan(tmp_path))


def test_privacy_symlink_and_index_symlink(tmp_path):
    temp_git(tmp_path, "init", "-q")
    target = put(tmp_path, "README.md")
    try:
        (tmp_path / "README.link").symlink_to(target)
    except (OSError, NotImplementedError):
        pass  # Windows不具备权限时仍测试Git索引中的链接。
    else:
        assert any("链接" in line for line in PUB.scan(tmp_path))
    blob = temp_git(tmp_path, "hash-object", "-w", "README.md").strip().decode("ascii")
    temp_git(tmp_path, "update-index", "--add", "--cacheinfo", "120000", blob, "README.link")
    assert any("Git禁止链接" in line for line in PUB.scan(tmp_path))


def test_privacy_clean_git_history(tmp_path):
    temp_git(tmp_path, "init", "-q")
    put(tmp_path, "README.md")
    temp_git(tmp_path, "add", "README.md")
    temp_git(tmp_path, "commit", "-q", "-m", "fixture")
    temp_git(tmp_path, "tag", "-a", "safe", "-m", "fixture annotation")
    assert PUB.scan_history(tmp_path) == []


def test_privacy_deleted_history_and_other_branch(tmp_path):
    temp_git(tmp_path, "init", "-q")
    payload = "sk" + "-" + "T" * 24
    put(tmp_path, "README.md", payload)
    temp_git(tmp_path, "add", "README.md")
    temp_git(tmp_path, "commit", "-q", "-m", "fixture")
    temp_git(tmp_path, "branch", "fixture-old")
    temp_git(tmp_path, "checkout", "-q", "--orphan", "fixture-clean")
    put(tmp_path, "README.md")
    temp_git(tmp_path, "add", "README.md")
    temp_git(tmp_path, "commit", "-q", "-m", "fixture clean")
    assert PUB.scan(tmp_path) == []
    result = PUB.scan_history(tmp_path)
    assert any("API密钥" in line for line in result)
    assert all(payload not in line for line in result)


def test_privacy_tag_only_history_and_annotation(tmp_path):
    temp_git(tmp_path, "init", "-q")
    put(tmp_path, ".env.fixture", "公开夹具禁止路径")
    temp_git(tmp_path, "add", ".env.fixture")
    temp_git(tmp_path, "commit", "-q", "-m", "fixture")
    temp_git(tmp_path, "tag", "old")
    temp_git(tmp_path, "checkout", "-q", "--orphan", "fixture-clean")
    temp_git(tmp_path, "rm", "-q", "-f", ".env.fixture")
    put(tmp_path, "README.md")
    temp_git(tmp_path, "add", "README.md")
    temp_git(tmp_path, "commit", "-q", "-m", "fixture clean")
    # 删除初始分支后仍必须扫描仅由标签保留的历史。
    for name in temp_git(tmp_path, "branch", "--format=%(refname:short)").decode("utf-8").splitlines():
        if name != "fixture-clean":
            temp_git(tmp_path, "branch", "-D", name)
    payload = "Bear" + "er " + "V" * 24
    temp_git(tmp_path, "tag", "-a", "note", "-m", payload)
    result = PUB.scan_history(tmp_path)
    assert any("历史包含禁止路径" in line for line in result)
    assert any("Bearer凭据" in line for line in result)
    assert all(payload not in line for line in result)


def test_privacy_noncommit_tag_and_shallow_fail_closed(tmp_path):
    temp_git(tmp_path, "init", "-q")
    put(tmp_path, "README.md")
    temp_git(tmp_path, "add", "README.md")
    temp_git(tmp_path, "commit", "-q", "-m", "fixture")
    oid = temp_git(tmp_path, "hash-object", "README.md").strip().decode("ascii")
    temp_git(tmp_path, "tag", "blob", oid)
    assert any("无法完整检查" in line for line in PUB.scan_history(tmp_path))
    temp_git(tmp_path, "tag", "-d", "blob")
    commit = temp_git(tmp_path, "rev-parse", "HEAD").decode("ascii")
    put(tmp_path, ".git/shallow", commit)
    assert any("无法完整检查" in line for line in PUB.scan_history(tmp_path))


def test_privacy_sync_only_hashes_and_section_numbers(tmp_path):
    source = tmp_path / "skill"
    state = tmp_path / "repo/.sync/state.json"
    payload = "sk" + "-" + "U" * 24
    put(source, "SKILL.md", "# 夹具\n## " + payload + "\n私人正文\n")
    put(tmp_path / "repo", ".sync/state.json", json.dumps({"skill_path": str(source), "sections": [payload]}))
    with patch.object(PUB, "SKILL", source), patch.object(PUB, "STATE", state):
        rc, output = capture(PUB.cmd_sync, argparse.Namespace())
        assert rc == 0
        assert payload not in output and str(source) not in output
        data = json.loads(state.read_text(encoding="utf-8"))
        assert set(data) == {"skill_sha256", "section_hashes"}
        assert all(len(value) == 64 for value in [data["skill_sha256"], *data["section_hashes"]])
        assert payload not in state.read_text(encoding="utf-8")
        assert str(source) not in state.read_text(encoding="utf-8")
        assert capture(PUB.cmd_sync, argparse.Namespace())[0] == 0
        put(source, "SKILL.md", "# 夹具\n## " + payload + "\n更新正文\n")
        assert "2" in capture(PUB.cmd_sync, argparse.Namespace())[1]
    with patch.object(PUB, "SKILL", tmp_path / "missing"), patch.object(PUB, "STATE", state):
        rc, output = capture(PUB.cmd_sync, argparse.Namespace())
        assert rc == 1 and "ENBANC_SKILL" in output
        assert str(tmp_path) not in output


def test_privacy_push_checks_before_git_and_no_skip(tmp_path):
    events = []
    def reject(args):
        events.append("check")
        return 1
    def forbidden(*args, **kwargs):
        raise AssertionError("失败检查后不得调用Git或联网")
    with patch.object(PUB, "cmd_check", reject), patch.object(PUB, "git", forbidden):
        assert PUB.cmd_push(argparse.Namespace(yes=True)) == 1
        assert PUB.cmd_push(argparse.Namespace(yes=False)) == 1
    assert events == ["check", "check"]
    with patch.object(PUB, "REPO", tmp_path), patch.object(PUB, "run_tests", return_value=(False, "测试失败")) as runner:
        rc, _ = capture(PUB.cmd_check, argparse.Namespace(skip_tests=True))
        assert rc == 1 and runner.call_count == 1
    with patch("sys.argv", ["publish", "check", "--skip-tests"]):
        try:
            capture(PUB.main)
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("不得提供跳过测试参数")


def test_privacy_remote_credentials_hidden(tmp_path):
    temp_git(tmp_path, "init", "-q")
    with patch.object(PUB, "REPO", tmp_path):
        assert not PUB.check_remote()
        host = "github" + ".com"
        value = "https://" + "fixture:" + "hidden-value" + "@" + host + "/fixture/repo"
        temp_git(tmp_path, "config", "remote.origin.url", value)
        assert not PUB.check_remote()
        temp_git(tmp_path, "config", "remote.origin.url", "https://" + host + "/fixture/repo")
        assert PUB.check_remote()
        temp_git(tmp_path, "config", "remote.origin.pushurl", "https://" + host + "/fixture/repo?" + "auth=" + "hidden")
        assert not PUB.check_remote()


def test_privacy_run_tests_utf8_timeout_no_exception_values():
    def stub(*args, **kwargs):
        assert kwargs["encoding"] == "utf-8" and kwargs["timeout"] > 0
        assert kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
        return subprocess.CompletedProcess(args[0], 0, "敏感异常内容\n42/42 passed\n", "敏感错误")
    with patch.object(PUB.subprocess, "run", stub):
        assert PUB.run_tests() == (True, "42/42 passed")
    with patch.object(PUB.subprocess, "run", side_effect=subprocess.TimeoutExpired("fixture", 1)):
        ok, message = PUB.run_tests()
        assert not ok and "超时" in message


def test_privacy_non_git_check_and_stage_never_add(tmp_path):
    put(tmp_path, "README.md")
    put(tmp_path, "LICENSE")
    put(tmp_path, "docs/reference/rules.md")
    put(tmp_path, "SKILL.md", (ROOT / "SKILL.md").read_text(encoding="utf-8"))
    put(tmp_path, "references/safety-rules.md", "通用规则")
    with patch.object(PUB, "REPO", tmp_path), patch.object(PUB, "run_tests", return_value=(True, "1/1 passed")):
        assert capture(PUB.cmd_check, argparse.Namespace())[0] == 0
        assert capture(PUB.cmd_stage, argparse.Namespace())[0] == 0
        temp_git(tmp_path, "init", "-q")
        assert capture(PUB.cmd_stage, argparse.Namespace())[0] == 0
        assert temp_git(tmp_path, "ls-files") == b""


def test_privacy_push_dry_run_never_contacts_remote(tmp_path):
    temp_git(tmp_path, "init", "-q")
    put(tmp_path, "README.md")
    temp_git(tmp_path, "add", "README.md")
    temp_git(tmp_path, "commit", "-q", "-m", "fixture")
    value = "https://" + "github" + ".com/fixture/repo"
    temp_git(tmp_path, "config", "remote.origin.url", value)
    real_git = PUB.git
    events = []
    def checked(args):
        events.append("check")
        return 0
    def local_only(root, *args, **kwargs):
        assert events and events[0] == "check"
        assert "push" not in args, "测试禁止联网"
        return real_git(root, *args, **kwargs)
    with patch.object(PUB, "REPO", tmp_path), patch.object(PUB, "cmd_check", checked), patch.object(PUB, "git", local_only):
        rc, output = capture(PUB.cmd_push, argparse.Namespace(yes=False))
        assert rc == 0
        assert value not in output
        assert "fixture" not in output


def test_privacy_same_branch_deleted_blob_and_commit_message(tmp_path):
    temp_git(tmp_path, "init", "-q")
    payload = "sk" + "-" + "W" * 24
    put(tmp_path, "README.md", payload)
    temp_git(tmp_path, "add", "README.md")
    temp_git(tmp_path, "commit", "-q", "-m", "fixture")
    put(tmp_path, "README.md")
    temp_git(tmp_path, "add", "README.md")
    temp_git(tmp_path, "commit", "-q", "-m", "fixture clean")
    result = PUB.scan_history(tmp_path)
    assert any("API密钥" in line for line in result)
    assert all(payload not in line for line in result)
    temp_git(tmp_path, "commit", "--allow-empty", "-q", "-m", payload)
    assert any("[历史提交]" in line and "API密钥" in line for line in PUB.scan_history(tmp_path))


def test_privacy_tracked_ignored_public_file(tmp_path):
    temp_git(tmp_path, "init", "-q")
    put(tmp_path, ".gitignore", "README.md\n")
    put(tmp_path, "README.md")
    temp_git(tmp_path, "add", "-f", "README.md")
    assert any("已跟踪被忽略文件" in line for line in PUB.scan(tmp_path))


def test_privacy_readme_parity_preserved(tmp_path):
    put(tmp_path, "README.md", "## 模型\n`delivered`\n")
    put(tmp_path, "README.zh-CN.md", "## 模型\n`delivered`\n")
    with patch.object(PUB, "REPO", tmp_path):
        assert PUB.readme_parity() == []
        put(tmp_path, "README.zh-CN.md", "## 模型\n## 多余\n")
        assert PUB.readme_parity()
