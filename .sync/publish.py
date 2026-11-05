#!/usr/bin/env python3
"""本机发布门禁：白名单、隐私扫描、测试与历史检查；只有 push 可联网。"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
SKILL = Path(os.environ.get("ENBANC_SKILL", Path.home() / ".workbuddy" / "skills" / "enbanc"))
STATE = REPO / ".sync" / "state.json"
TIMEOUT = 120
# Only the explicitly authorized project contact address may be public.
PUBLIC_CONTACT = "pyq3367@zoho.com"
CONTACT_FILES = {"README.md", "README.zh-CN.md", "docs/005-privacy.md"}
# Git records the author in the commit object itself, so the authorized
# address is expected to appear there too.
CONTACT_CONTEXTS = CONTACT_FILES | {"[历史提交]", "[历史标签]"}
EMAIL_PATTERN = re.compile(r"(?i)[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}")
PUBLIC_FILES = {"LICENSE", "CHANGELOG.md", "pyproject.toml", ".gitignore",
                ".sync/publish.py", "SKILL.md", "examples/demo/run.py",
                "examples/safety-rules.md", "references/safety-rules.md"}
LOCAL_DIRS = {"__pycache__", ".venv", "venv", "build", "dist", ".pytest_cache",
              ".mypy_cache", "runs", "logs"}
LEAK_PATTERNS = [
    (r"(?<![\w])(?:\d{1,3}\.){3}\d{1,3}(?![\w])", "IPv4地址"),
    (r"(?<![\w:])(?:[0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F:.%_-]*", "IPv6地址"),
    (r"(?<![\w])[A-Za-z]:[\\/]+[^\s\x00`'\"<>|]+", "Windows绝对路径"),
    (r"\\\\[A-Za-z0-9_.-]+\\[^\s`'\"<>|]+", "Windows绝对路径"),
    (r"/(?:Users|home)/[^/\s`'\"<>]+", "用户目录路径"),
    (r"\bsk-[A-Za-z0-9_-]{16,}\b", "API密钥"),
    (r"\bBearer[ \t]+[A-Za-z0-9._~+/=-]{8,}", "Bearer凭据"),
    (r"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----", "私钥"),
    (r"(?i)\b[\w.-]+@(?:cn|intl)\b", "私有模型标识"),
    (r"(?i)\b(?:[a-z0-9-]+\.)+(?:lan|local|internal|intranet|corp|home|private)\b", "私有域名"),
]
ASSIGNMENT = re.compile(
    r"(?i)(?<![\w])(?:api[_-]?key|(?:api|access|auth|refresh)[_-]?token|token|"
    r"secret(?:[_-]?key)?|client[_-]?secret|password|passwd|credential)"
    r"[\"']?\s*[:=]\s*(.*)")
PLACEHOLDER = re.compile(
    r"(?i)(?:|none|null|true|false|0|<[^<>]+>|\{[^{}]+\}|\$\{[^{}]+\}|"
    r"your[_-][\w-]+|change[_-]?me|placeholder|example|dummy|redacted)")


class GateError(Exception):
    """仅携带可公开的固定错误类别。"""


def diagnostic(name: str, line: int, category: str) -> str:
    # 文件名也可能包含私人值；这种情况只显示不含值的相对代号。
    if re.search(r"[\x00-\x1f\x7f]", name) or text_findings(name) or EMAIL_PATTERN.search(name):
        name = "[非公开文件名]"
    return f"{name}:{line}  {category}"


def publishable(name: str) -> bool:
    p = PurePosixPath(name)
    parts = p.parts
    if not parts or p.is_absolute() or any(x in {"..", "."} for x in parts) or "\\" in name:
        return False
    if any(x in LOCAL_DIRS or x == ".git" or x.endswith(".egg-info") for x in parts):
        return False
    if any(x.startswith(".env") or x.lower().endswith((".log", ".jsonl")) for x in parts):
        return False
    if name in PUBLIC_FILES:
        return True
    if len(parts) == 1 and p.name.startswith("README"):
        return True
    return ((len(parts) >= 3 and parts[:2] == ("src", "enbanc") and p.suffix == ".py")
            or (len(parts) == 2 and parts[0] == "tests" and p.suffix == ".py")
            or (len(parts) >= 2 and parts[0] == "docs" and p.suffix == ".md")
            # Repository housekeeping: issue/PR templates, security policy and CI.
            # Still subject to the same privacy scan as every other file.
            or (len(parts) >= 2 and parts[0] == ".github" and p.suffix in {".md", ".yml", ".yaml"}))


def text_findings(text: str) -> list[tuple[int, str]]:
    findings = []
    for no, line in enumerate(text.splitlines(), 1):
        for pattern, label in LEAK_PATTERNS:
            for hit in re.finditer(pattern, line):
                if label in {"IPv4地址", "IPv6地址"}:
                    if label == "IPv6地址" and not re.search(r"[0-9a-fA-F]", hit.group()):
                        if line.strip() != hit.group() and "[" + hit.group() + "]" not in line:
                            continue  # 分类符号或规则字符串中的双冒号不是地址值。
                    try:
                        ipaddress.ip_address(hit.group())
                    except ValueError:
                        continue
                if label == "私有域名" and hit.group() == "Path" + ".home" and line[hit.end():].startswith("()"):
                    continue  # 标准库调用语法，不是作者的私有域名。
                findings.append((no, label))
        for hit in ASSIGNMENT.finditer(line):
            value = hit.group(1).strip()
            if value.startswith(("'", '"')):
                quote = value[0]
                end = value.find(quote, 1)
                value = value[1:end] if end >= 0 else value[1:]
            else:
                value = re.split(r"[\s,;#)}\]]", value, maxsplit=1)[0]
                # 代码取环境变量不是嵌入凭据；只接受这两种明确调用。
                if value.startswith(("os.environ", "os.getenv(")):
                    continue
            if not PLACEHOLDER.fullmatch(value):
                findings.append((no, "非占位凭据赋值"))
    return sorted(set(findings))


def scan_bytes(name: str, data: bytes) -> list[str]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return [diagnostic(name, 1, "非UTF8或未知二进制")]
    if any(ord(c) < 32 and c not in "\t\r\n" for c in text):
        return [diagnostic(name, 1, "未知二进制")]
    findings = [diagnostic(name, no, label) for no, label in text_findings(text)]
    for no, line in enumerate(text.splitlines(), 1):
        for hit in EMAIL_PATTERN.finditer(line):
            address = hit.group()
            if address == PUBLIC_CONTACT and name in CONTACT_CONTEXTS:
                continue
            if address == PUBLIC_CONTACT and name == ".sync/publish.py" and line.strip() == 'PUBLIC_CONTACT = "' + PUBLIC_CONTACT + '"':
                continue
            # Reserved domains are safe examples, not personal contact details.
            domain = address.rsplit("@", 1)[1].lower()
            if domain in {"example.com", "example.org", "example.net", "example.invalid"}:
                continue
            findings.append(diagnostic(name, no, "未授权邮箱或联系方式"))
    return sorted(set(findings))


def git(root: Path, *args: str, input_data: bytes | None = None,
        ok_codes: tuple[int, ...] = (0,)) -> bytes:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_")}
    env.update(GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1",
               GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0",
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    if (not (root / ".git").is_dir() or (root / ".git").is_symlink()
            or getattr((root / ".git").lstat(), "st_file_attributes", 0) & 0x400):
        raise GateError("不支持外部Git目录")
    try:
        r = subprocess.run(["git", "--no-replace-objects", "-c", "core.fsmonitor=false", *args],
                           cwd=root, input=input_data, capture_output=True, timeout=TIMEOUT, env=env)
    except (OSError, subprocess.TimeoutExpired):
        raise GateError("Git读取失败或超时") from None
    if r.returncode not in ok_codes:
        raise GateError("Git读取失败")
    return r.stdout


def is_git(root: Path) -> bool:
    # 不向父目录借用仓库，也不读取用户的全局配置。
    return os.path.lexists(root / ".git")


def index_entries(root: Path) -> list[tuple[str, str, str]]:
    entries = []
    for record in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not record:
            continue
        metadata, name = record.split(b"\t", 1)
        mode, oid, stage = metadata.decode("ascii").split()
        if stage != "0":
            raise GateError("Git索引存在未解决冲突")
        entries.append((name.decode("utf-8"), mode, oid))
    return entries


def local_generated(name: str) -> bool:
    parts = PurePosixPath(name).parts
    return (any(x in LOCAL_DIRS or x.endswith(".egg-info") for x in parts[:-1])
            or name == ".sync/state.json")


def scan(root: Path) -> list[str]:
    problems = []
    try:
        entries = index_entries(root) if is_git(root) else []
        tracked = {name for name, _, _ in entries}
        pending = [root]
        while pending:
            directory = pending.pop()
            for p in sorted(directory.iterdir()):
                name = p.relative_to(root).as_posix()
                if p.is_symlink() or getattr(p.lstat(), "st_file_attributes", 0) & 0x400:
                    problems.append(diagnostic(name, 1, "禁止符号链接或目录联接"))
                    continue
                if name == ".git":
                    continue
                if p.is_dir():
                    pending.append(p)
                    continue
                if not p.is_file():
                    problems.append(diagnostic(name, 1, "不支持的文件类型"))
                    continue
                if name not in tracked and not publishable(name):
                    if local_generated(name):
                        continue
                    if is_git(root):
                        ignored = git(root, "check-ignore", "--no-index", "--stdin", "-z",
                                      input_data=name.encode("utf-8") + b"\0", ok_codes=(0, 1))
                        if ignored:
                            continue
                if not publishable(name):
                    problems.append(diagnostic(name, 1, "不在公开文件白名单"))
                # 不按后缀筛选；被拒绝的未知文本也接受隐私扫描。
                problems.extend(scan_bytes(name, p.read_bytes()))
        problems.extend(scan_index(root, entries))
    except (OSError, ValueError, UnicodeError, GateError):
        problems.append("[检查]:1  文件或Git索引检查失败")
    return sorted(set(problems))


def scan_index(root: Path, entries: list[tuple[str, str, str]]) -> list[str]:
    problems = []
    for name, mode, oid in entries:
        if not publishable(name):
            problems.append(diagnostic(name, 1, "Git已跟踪禁止路径（忽略规则不豁免）"))
        elif git(root, "check-ignore", "--no-index", "--stdin", "-z",
                 input_data=name.encode("utf-8") + b"\0", ok_codes=(0, 1)):
            problems.append(diagnostic(name, 1, "Git已跟踪被忽略文件，拒绝发布"))
        if mode not in {"100644", "100755"}:
            problems.append(diagnostic(name, 1, "Git禁止链接或非普通文件"))
            continue
        problems.extend(scan_bytes(name, git(root, "cat-file", "blob", oid)))
    return problems


def scan_history(root: Path) -> list[str]:
    if not is_git(root):
        return []
    problems = []
    try:
        if git(root, "rev-parse", "--is-shallow-repository").strip() != b"false":
            raise GateError("浅历史不能完整检查")
        if git(root, "replace", "-l").strip() or (root / ".git" / "info" / "grafts").exists():
            raise GateError("替换或嫁接历史不能完整检查")
        tips = git(root, "for-each-ref", "--format=%(objectname)", "refs/heads", "refs/tags").splitlines()
        # detached HEAD 也不能成为盲区；尚无提交的仓库可没有 HEAD。
        if tips:
            tips.append(git(root, "rev-parse", "--verify", "HEAD").strip())
        else:
            try:
                tips = [git(root, "rev-parse", "--verify", "HEAD").strip()]
            except GateError:
                # 只允许合法的未出生符号分支；损坏或游离HEAD不能豁免。
                head = git(root, "symbolic-ref", "--quiet", "HEAD").strip()
                if not head.startswith(b"refs/heads/"):
                    raise GateError("异常HEAD")
                return []
        commits = []
        for raw in set(tips):
            oid = raw.decode("ascii")
            seen = set()
            while True:
                if oid in seen:
                    raise GateError("异常标签历史")
                seen.add(oid)
                kind = git(root, "cat-file", "-t", oid).strip()
                if kind == b"commit":
                    commits.append(oid)
                    break
                if kind != b"tag":
                    raise GateError("非提交标签无法完整检查，拒绝发布")
                data = git(root, "cat-file", "tag", oid)
                problems.extend(scan_bytes("[历史标签]", data))
                first = data.splitlines()[0].split()
                if len(first) != 2 or first[0] != b"object":
                    raise GateError("异常标签历史")
                oid = first[1].decode("ascii")
        # 校验全部可达对象可读取；不相信工作区或当前分支代表整个历史。
        objects = git(root, "rev-list", "--objects", "--no-object-names", *commits).splitlines()
        for oid in set(objects):
            git(root, "cat-file", "-e", oid.decode("ascii"))
        checked = set()
        for oid in git(root, "rev-list", *commits).splitlines():
            commit = oid.decode("ascii")
            problems.extend(scan_bytes("[历史提交]", git(root, "cat-file", "commit", commit)))
            for record in git(root, "ls-tree", "-r", "-z", commit).split(b"\0"):
                if not record:
                    continue
                metadata, raw_name = record.split(b"\t", 1)
                mode, kind, blob = metadata.decode("ascii").split()
                name = raw_name.decode("utf-8")
                if (name, mode, blob) in checked:
                    continue
                checked.add((name, mode, blob))
                if not publishable(name):
                    problems.append(diagnostic(name, 1, "历史包含禁止路径"))
                if kind != "blob" or mode not in {"100644", "100755"}:
                    problems.append(diagnostic(name, 1, "历史包含链接或非普通文件"))
                    continue
                problems.extend(scan_bytes(name, git(root, "cat-file", "blob", blob)))
    except (OSError, ValueError, UnicodeError, GateError):
        problems.append("[历史]:1  无法完整检查所有本地分支及标签历史，拒绝发布")
    return sorted(set(problems))


def run_tests() -> tuple[bool, str]:
    env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    try:
        r = subprocess.run([sys.executable, "-B", "tests/run.py"], cwd=REPO,
                           capture_output=True, text=True, encoding="utf-8", errors="strict",
                           timeout=TIMEOUT, env=env)
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        return False, "测试执行失败或超时"
    # 不转发异常文本或测试内容；只接收规定格式的汇总。
    summary = re.findall(r"(?m)^(\d+/\d+ passed)$", r.stdout)
    return r.returncode == 0, summary[-1] if summary else "测试未返回安全汇总"


PARITY_TERMS = ("delivered", "needs_human", "unverified", "blocked", "stalled",
                "document", "page", "deck", "code", "enbanc stage", "publish.py check")


def readme_parity() -> list[str]:
    en_p, zh_p = REPO / "README.md", REPO / "README.zh-CN.md"
    if not zh_p.exists():
        return []
    en = en_p.read_text(encoding="utf-8")
    zh = zh_p.read_text(encoding="utf-8")
    problems = []
    for label, a, b in (
        ("H2章节", sum(x.startswith("## ") for x in en.splitlines()), sum(x.startswith("## ") for x in zh.splitlines())),
        ("H3章节", sum(x.startswith("### ") for x in en.splitlines()), sum(x.startswith("### ") for x in zh.splitlines())),
        ("代码围栏", en.count("```"), zh.count("```")),
        ("表格行", sum(x.strip().startswith("|") for x in en.splitlines()), sum(x.strip().startswith("|") for x in zh.splitlines())),
    ):
        if a != b:
            problems.append(f"{label}数量不同：en={a} zh={b}")
    if any((term in en) != (term in zh) for term in PARITY_TERMS):
        problems.append("README机制标识不一致")
    literals = {m.strip() for m in re.findall(r"`([^`\n]{1,40})`", en)}
    if any(literal not in zh for literal in literals):
        problems.append("README.zh-CN.md缺少英文README中的字面标识")
    return problems


def cmd_check(a) -> int:
    print("== 发布文件与隐私检查 ==")
    problems = scan(REPO) + scan_history(REPO)
    if problems:
        for problem in sorted(set(problems)):
            print("  拒绝 " + problem)
        return 1
    print("  通过")
    print("== 测试 ==")
    ok, summary = run_tests()
    print("  " + summary)
    if not ok:
        return 1
    if any(not (REPO / name).is_file() for name in ("README.md", "LICENSE", "docs/reference/rules.md")):
        print("  必需公开文档缺失")
        return 1
    issues = validate_skill_entry(REPO)
    if issues:
        print("  技能入口验证失败")
        return 1
    try:
        drift = readme_parity()
    except (OSError, UnicodeError):
        print("  README一致性检查失败")
        return 1
    for problem in drift:
        print("  " + problem)
    if drift:
        return 1
    print("  README一致；本机检查通过")
    return 0


def cmd_sync(a) -> int:
    # 只读取用户自己的 SKILL.md；不访问 memory/models 或其他配置。
    source = SKILL / "SKILL.md"
    try:
        if any(p.is_symlink() or (p.exists() and getattr(p.lstat(), "st_file_attributes", 0) & 0x400)
               for p in (source, SKILL, STATE, STATE.parent)):
            raise OSError
        text = source.read_text(encoding="utf-8")
        previous = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
        if not isinstance(previous, dict):
            previous = {}
    except (OSError, UnicodeError, ValueError):
        print("无法读取本机技能或同步状态；请显式设置 ENBANC_SKILL", file=sys.stderr)
        return 1
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    sections = re.split(r"(?m)(?=^##\s+)", text)
    hashes = [hashlib.sha256(section.encode("utf-8")).hexdigest() for section in sections]
    old = previous.get("section_hashes", [])
    if not isinstance(old, list):
        old = []
    changed = [i + 1 for i in range(max(len(old), len(hashes)))
               if (old[i] if i < len(old) else None) != (hashes[i] if i < len(hashes) else None)]
    if digest == previous.get("skill_sha256") and not changed:
        print("技能未变更")
    else:
        print("技能变更；章节编号：" + ", ".join(map(str, changed)))
        print("请人工核对公开文档后运行 publish.py check")
    # 总是覆盖旧格式，不能保留路径、章节标题或正文。
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({"skill_sha256": digest, "section_hashes": hashes}, indent=2), encoding="utf-8")
    except OSError:
        print("无法保存脱敏同步状态", file=sys.stderr)
        return 1
    return 0


def validate_skill_entry(root: Path) -> list[str]:
    try:
        content = (root / "SKILL.md").read_text(encoding="utf-8")
        match = re.match(r"^---\r?\n(.*?)\r?\n---", content, re.S)
        if not match:
            return ["技能frontmatter缺失"]
        metadata = match.group(1)
        fields = ("name", "description", "display_name", "display_name_en",
                  "description_zh", "description_en", "version", "author", "agent_created")
        if any(not re.search(r"(?m)^" + re.escape(field) + r":\s*\S", metadata) for field in fields):
            return ["技能必需字段缺失"]
        if not re.search(r"(?m)^name:\s*enbanc\s*$", metadata):
            return ["技能名称不匹配"]
        if not (root / "references/safety-rules.md").is_file():
            return ["技能通用规则缺失"]
        return []
    except (OSError, UnicodeError):
        return ["技能入口读取失败"]


def cmd_export_skill(a) -> int:
    # Never archive the whole private skill or the whole working tree.
    rc = cmd_check(a)
    if rc:
        return rc
    issues = validate_skill_entry(REPO)
    if issues:
        print("技能验证失败")
        return 1
    output_dir = REPO / "dist"
    if output_dir.is_symlink() or (output_dir.exists() and getattr(output_dir.lstat(), "st_file_attributes", 0) & 0x400):
        print("导出目录不安全")
        return 1
    output_dir.mkdir(exist_ok=True)
    target = output_dir / "enbanc-skill.zip"
    if os.path.lexists(target):
        print("已有导出包；请显式选择保留或重新生成，未覆盖")
        return 2
    names = ("SKILL.md", "references/safety-rules.md", "LICENSE")
    with zipfile.ZipFile(target, "x", zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            archive.writestr("enbanc/" + name, (REPO / name).read_bytes())
    with zipfile.ZipFile(target) as archive:
        if archive.testzip() is not None or set(archive.namelist()) != {"enbanc/" + name for name in names}:
            print("导出校验失败，禁止发布")
            return 1
        if any(scan_bytes(name, archive.read("enbanc/" + name)) for name in names):
            print("导出隐私校验失败，禁止发布")
            return 1
    print("已生成 dist/enbanc-skill.zip；校验通过，未上传")
    return 0


def cmd_stage(a) -> int:
    rc = cmd_check(a)
    if rc:
        return rc
    if not is_git(REPO):
        print("非Git目录；只完成本机检查，未暂存任何文件")
        return 0
    try:
        dirty = bool(git(REPO, "status", "--porcelain", "-z"))
    except GateError:
        print("无法检查暂存状态")
        return 1
    print("存在待人工处理的改动；未自动add" if dirty else "没有待处理改动")
    return 0


def check_remote() -> bool:
    origin = False
    try:
        dangerous = git(REPO, "config", "--local", "--get-regexp",
                        r"^(url\.|include|core\.(sshcommand|gitproxy|hookspath)|remote\..*\.(mirror|receivepack|vcs)|extensions\.)",
                        ok_codes=(0, 1))
        if dangerous:
            return False
        data = git(REPO, "config", "--local", "--get-regexp", r"^remote\..*\.(url|pushurl)$")
        records = [line.split(None, 1) for line in data.decode("utf-8").splitlines()]
        if not records:
            return False
        for record in records:
            if len(record) != 2:
                return False
            value = record[1]
            if any(c.isspace() for c in value) or text_findings(value):
                return False
            if value.startswith("https://"):
                parsed = urlsplit(value)
                if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                    return False
            elif not re.fullmatch(r"git@[A-Za-z0-9.-]+:[A-Za-z0-9_./-]+", value):
                return False
            if record[0] == "remote.origin.url":
                origin = True
        return origin
    except (ValueError, UnicodeError, GateError):
        return False


def cmd_push(a) -> int:
    # 即使 --yes，也必须先完成所有门禁；不输出远程地址或分支名。
    rc = cmd_check(a)
    if rc:
        return rc
    if not is_git(REPO):
        print("非Git目录；拒绝推送")
        return 2
    try:
        if git(REPO, "status", "--porcelain", "-z").strip():
            print("存在未提交改动；拒绝推送")
            return 2
        branch = git(REPO, "symbolic-ref", "--quiet", "HEAD").decode("utf-8").strip()
        if not branch.startswith("refs/heads/") or text_findings(branch):
            raise GateError("分支不安全")
    except (GateError, UnicodeError):
        print("无法确认安全分支；拒绝推送")
        return 2
    if not check_remote():
        print("未配置安全远程或远程地址含凭据；拒绝推送")
        return 2
    if not a.yes:
        print("仅预检；未联网。显式 --yes 才允许推送")
        return 0
    try:
        git(REPO, "-c", "push.followTags=false", "push", "--no-follow-tags", "origin", f"{branch}:{branch}")
    except GateError:
        print("推送失败（远程输出已隐藏）")
        return 2
    print("推送完成")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="publish", description="默认仅本机检查的公开发布门禁")
    ap.add_argument("--skill", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="白名单、隐私、历史、测试与README检查").set_defaults(fn=cmd_check)
    sub.add_parser("sync", help="仅保存技能与章节哈希").set_defaults(fn=cmd_sync)
    sub.add_parser("stage", help="先检查；不自动暂存").set_defaults(fn=cmd_stage)
    sub.add_parser("export-skill", help="仅打包公开技能入口与规则，不上传").set_defaults(fn=cmd_export_skill)
    p = sub.add_parser("push", help="先完整检查；需要显式 --yes")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=cmd_push)
    a = ap.parse_args()
    if a.skill:
        global SKILL
        SKILL = Path(a.skill)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
