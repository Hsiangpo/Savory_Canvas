import ast
import os
import sys

# 排除检查的目录
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", "data", "storage", "migrations", ".venv", "venv"}
# 排除检查的文件
EXCLUDE_FILES = {"__init__.py"}
# 限制阈值
MAX_LINES_PER_FILE = 1000
MAX_LINES_PER_FUNC = 200
MAX_FILES_PER_DIR = 10


def configure_console_output() -> None:
    stream = getattr(sys, "stdout", None)
    if stream is not None and hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(errors="replace")
        except (OSError, ValueError):
            pass
    err_stream = getattr(sys, "stderr", None)
    if err_stream is not None and hasattr(err_stream, "reconfigure"):
        try:
            err_stream.reconfigure(errors="replace")
        except (OSError, ValueError):
            pass

def check_file_limits(file_path: str) -> list[str]:
    errors = []
    
    # 检查编码及文件行数
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        return [f"{file_path}: 非 UTF-8 编码！"]

    if len(lines) >= MAX_LINES_PER_FILE:
        errors.append(f"{file_path}: 行数超限 ({len(lines)} >= {MAX_LINES_PER_FILE})")

    content = "".join(lines)
    try:
        tree = ast.parse(content, filename=file_path)
    except SyntaxError as e:
        errors.append(f"{file_path}: 语法错误 ({e})")
        return errors

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # node.end_lineno is available in Python 3.8+
            if hasattr(node, 'end_lineno') and node.end_lineno is not None:
                func_lines = node.end_lineno - node.lineno + 1
                if func_lines >= MAX_LINES_PER_FUNC:
                    errors.append(
                        f"{file_path}: 函数 '{node.name}' 行数超限 "
                        f"({func_lines} >= {MAX_LINES_PER_FUNC})"
                    )
    return errors

def main():
    configure_console_output()
    backend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backend')
    
    if not os.path.exists(backend_dir):
        print(f"Error: {backend_dir} 不存在。")
        sys.exit(1)

    all_errors = []

    for root, dirs, files in os.walk(backend_dir):
        # 排除不需要检查的目录
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        
        # 统计本目录下的有效源文件数量（这里以 .py 结尾作为统计，或者只管文件总数）
        # 按照题目要求：“一个文件夹下不能超过10个文件”
        valid_files = [f for f in files if f.endswith('.py') and f not in EXCLUDE_FILES]
        
        if len(valid_files) > MAX_FILES_PER_DIR:
            # 去除绝对路径，仅显示相对 backend 的路径
            rel_dir = os.path.relpath(root, backend_dir)
            all_errors.append(f"目录 {rel_dir}: 文件数量超限 ({len(valid_files)} > {MAX_FILES_PER_DIR})")

        for file in files:
            if file.endswith('.py'):
                file_path = os.path.join(root, file)
                file_errors = check_file_limits(file_path)
                if file_errors:
                    all_errors.extend(file_errors)

    if all_errors:
        print("❌ 门禁检查未通过！发现以下超限问题：\n")
        for err in all_errors:
            print("  - " + err)
        print("\n请拆分文件或重构代码，禁止删除健壮性代码来凑行数！")
        sys.exit(1)
    else:
        print("✅ 门禁检查通过！所有后端代码均符合规范（文件 <1000行，函数 <200行，单目录文件 <=10个，UTF-8编码）。")
        sys.exit(0)

if __name__ == "__main__":
    main()
