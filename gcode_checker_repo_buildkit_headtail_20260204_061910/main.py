import os
import re
import json
import bisect
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter import simpledialog
from typing import Any, Dict, List, Optional, Tuple

# ============================
# （可選）拖拉開檔支援：tkinterdnd2
# pip install tkinterdnd2
# ============================
DND_AVAILABLE = False
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore
    DND_AVAILABLE = True
except Exception:
    DND_AVAILABLE = False
    DND_FILES = None
    TkinterDnD = None


# ============================
# 中文狀態
# ============================
STATUS_OK = "通過"
STATUS_MISSING = "缺少"
STATUS_ERROR = "錯誤"
EPS = 1e-12

# ============================
# 另存修正版（路徑刪除）內部參數（UI 不顯示）
# ============================
TRIM_START_Z_MAX = 0.1
TRIM_END_Z_MIN = 10.0


# ============================
# 灰色系介面
# ============================
BG = "#6B6B6B"
PANEL = "#5E5E5E"
PANEL2 = "#525252"
FG = "#F2F2F2"
MUTED = "#E0E0E0"
ACCENT = "#33D1FF"
OK_FG = "#B9FFCC"
DANGER_BG = "#B30000"
DANGER_FG = "#FFEAEA"

ROW_EVEN = "#595959"
ROW_ODD = "#545454"

SCROLL_TROUGH = "#2F2F2F"
SCROLL_BG = "#9A9A9A"
SCROLL_ACTIVE = "#B8B8B8"
SCROLL_PRESSED = "#D0D0D0"

SASH_BG = "#2B2F36"  # 科技感分隔條色


# ============================
# 歷程檔位置（可跨次啟動保留）
# ============================
def get_history_path() -> str:
    appdata = os.getenv("APPDATA")
    if appdata and os.path.isdir(appdata):
        base = os.path.join(appdata, "GCodeChecker")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "history.json")
    home = os.path.expanduser("~")
    base = os.path.join(home, ".gcode_checker")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "history.json")


HISTORY_PATH = get_history_path()


# ============================
# 讀檔（支援常見編碼 + 無副檔名）
# ============================
def read_text_with_fallback(path: str) -> Tuple[str, str]:
    encodings = ["utf-8-sig", "utf-8", "cp950", "big5", "gb18030"]
    last_err = None
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read(), enc
        except Exception as e:
            last_err = e
    try:
        with open(path, "rb") as f:
            raw = f.read()
        text = raw.decode("utf-8", errors="replace")
        return text, "utf-8（錯誤字元已替換）"
    except Exception as e:
        raise RuntimeError(f"讀檔失敗：{path}") from (last_err or e)


# ============================
# 行/列索引
# ============================
def build_line_starts(text: str) -> List[int]:
    starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def index_to_line_col(idx: int, line_starts: List[int]) -> Tuple[int, int]:
    line = bisect.bisect_right(line_starts, idx) - 1
    return line + 1, idx - line_starts[line] + 1


# ============================
# 註解遮罩（保留長度與索引）
# 支援：(...)、以及 ; 到行尾
# ============================
def mask_gcode_comments_keep_length(text: str) -> str:
    chars = list(text)
    in_paren = 0
    i = 0
    n = len(chars)

    while i < n:
        ch = chars[i]

        if ch == "(":
            in_paren += 1
            chars[i] = " "
            i += 1
            continue

        if ch == ")" and in_paren > 0:
            chars[i] = " "
            in_paren -= 1
            i += 1
            continue

        if in_paren > 0:
            if ch != "\n":
                chars[i] = " "
            i += 1
            continue

        if ch == ";":
            while i < n and chars[i] != "\n":
                chars[i] = " "
                i += 1
            continue

        i += 1

    return "".join(chars)


# ============================
# Token helpers（允許連寫：G90Z10.、T01M06）
# 避免 G54 誤判到 G543：後面不是數字才算 token 結束
# ============================
def token_pattern(token: str) -> str:
    return rf"(^|[^0-9A-Z])({re.escape(token)})(?=[^0-9]|$)"


def line_has_token(line: str, token: str) -> bool:
    return re.search(token_pattern(token), line, flags=re.IGNORECASE) is not None


def find_gcode_token(masked_text: str, token: str) -> Optional[re.Match]:
    return re.search(token_pattern(token), masked_text, flags=re.IGNORECASE | re.MULTILINE)


def extract_z_value(line: str) -> Optional[float]:
    m = re.search(
        r"(^|[^0-9A-Z])Z([+-]?(?:\d+\.\d*|\d*\.\d+|\d+))(?=[^0-9]|$)",
        line,
        flags=re.IGNORECASE
    )
    if not m:
        return None
    try:
        return float(m.group(2))
    except ValueError:
        return None


def collect_addr_numbers(masked_text: str, addr_letter: str) -> Tuple[List[str], Optional[int]]:
    addr_letter = addr_letter.upper()
    pattern = rf"(^|[^0-9A-Z])({addr_letter})([0-9]+)(?=[^0-9]|$)"

    first_idx: Optional[int] = None
    seen: Dict[int, str] = {}

    for m in re.finditer(pattern, masked_text, flags=re.IGNORECASE | re.MULTILINE):
        raw_num = m.group(3)
        try:
            num_int = int(raw_num)
        except ValueError:
            continue

        if num_int not in seen:
            seen[num_int] = raw_num

        idx = m.start(2)
        if first_idx is None or idx < first_idx:
            first_idx = idx

    nums_sorted = [seen[k] for k in sorted(seen.keys())]
    return nums_sorted, first_idx


# ============================
# 範圍顯示（第x-y行）
# ============================
def clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))


def make_range_text(lines: List[str], center_line: int, radius: int = 2) -> str:
    if not lines or center_line <= 0:
        return ""
    total = len(lines)
    s = clamp(center_line - radius, 1, total)
    e = clamp(center_line + radius, 1, total)
    return f"第{s}-{e}行" if s != e else f"第{s}行"


# ============================
# 通用結果格式
# ============================
def mk_result(
    rid: str,
    label: str,
    typ: str,
    status: str,
    evidence: str = "",
    pos_idx: Optional[int] = None,
    line: Optional[int] = None,
    col: Optional[int] = None,
) -> Dict[str, Any]:
    return {
        "id": rid,
        "label": label,
        "type": typ,
        "status": status,
        "evidence": evidence,
        "pos_idx": pos_idx,
        "line": line,
        "col": col,
    }


# ============================
# 數字解析：允許輸入「2.0」或「Z2.0」或「Z=2.0」
# ============================
def try_parse_float(s: str) -> Optional[float]:
    if s is None:
        return None
    s = str(s).strip().replace(",", "")
    if not s:
        return None
    m = re.search(r"([+-]?(?:\d+\.\d*|\d*\.\d+|\d+))", s)
    if not m:
        return None
    try:
        return float(m.group(1))
    except Exception:
        return None


# ============================
# ✅ 安全高度碰撞檢查（素材高度 vs G00 G90 Z）
# - 只比對：G90 模式下，同一行含 G00 + G90 + Z(正值)
# - Z<=0 不比對；Z>0 才判斷
# - 素材高度 < Z => 撞機警報
# ============================
def eval_safety_g00_g90_z_vs_material(
    masked_text: str,
    line_starts: List[int],
    material_height: float
) -> Dict[str, Any]:
    mask_lines = masked_text.splitlines(keepends=True)

    pos_mode: Optional[str] = None
    candidates: List[Tuple[int, float]] = []

    for i, ml in enumerate(mask_lines, start=1):
        if line_has_token(ml, "G90"):
            pos_mode = "G90"
        elif line_has_token(ml, "G91"):
            pos_mode = "G91"

        if pos_mode == "G90" and line_has_token(ml, "G00") and line_has_token(ml, "G90"):
            z = extract_z_value(ml)
            if z is None:
                continue
            if z <= 0.0 + EPS:
                continue
            candidates.append((i, z))

    if not candidates:
        return mk_result(
            "SAFE_G00G90Z",
            "安全高度碰撞檢查（素材高度 vs G00 G90 Z）",
            "安全設定",
            STATUS_OK,
            "未找到可比對的『G00 G90 Z(正值)』"
        )

    offenders = [(ln, z) for ln, z in candidates if material_height < z - EPS]
    if not offenders:
        max_ln, max_z = max(candidates, key=lambda x: x[1])
        evidence = f"素材高度={material_height:.4f}｜最大 G00 G90 Z={max_z:.4f}（第{max_ln}行）｜未觸發警報"
        return mk_result(
            "SAFE_G00G90Z",
            "安全高度碰撞檢查（素材高度 vs G00 G90 Z）",
            "安全設定",
            STATUS_OK,
            evidence,
            line=max_ln,
            col=1,
            pos_idx=line_starts[max_ln - 1] if 1 <= max_ln <= len(line_starts) else None
        )

    first_ln, first_z = offenders[0]
    pos_idx = line_starts[first_ln - 1] if 1 <= first_ln <= len(line_starts) else None

    preview_n = 6
    preview = offenders[:preview_n]
    preview_txt = "；".join([f"第{ln}行 Z={z:.4f}" for ln, z in preview])
    if len(offenders) > preview_n:
        preview_txt += f"；...（其餘{len(offenders)-preview_n}筆）"

    evidence = (
        f"素材高度={material_height:.4f}｜違規筆數={len(offenders)}｜"
        f"首筆：第{first_ln}行 Z={first_z:.4f}｜"
        f"判斷：素材高度({material_height:.4f}) < Z({first_z:.4f})｜"
        f"預覽：{preview_txt}"
    )

    return mk_result(
        "SAFE_G00G90Z",
        "安全高度碰撞檢查（素材高度 vs G00 G90 Z）",
        "安全設定",
        STATUS_ERROR,
        evidence,
        pos_idx=pos_idx,
        line=first_ln,
        col=1
    )


# ============================
# 強制檢查：宣告碼（G17 G40 G49 G80 G90）
# ============================
def eval_declaration_line(masked_text: str) -> Dict[str, Any]:
    required = ["G17", "G40", "G49", "G80", "G90"]
    missing = [t for t in required if find_gcode_token(masked_text, t) is None]
    if missing:
        return mk_result("DECL", "宣告碼檢查（G17 G40 G49 G80 G90）", "宣告碼", STATUS_MISSING, "缺少：" + ", ".join(missing))
    return mk_result("DECL", "宣告碼檢查（G17 G40 G49 G80 G90）", "宣告碼", STATUS_OK, "已找到所有宣告碼")


# ============================
# 回零檢查（G91 G28 Z0.0）
# ============================
def eval_home_return_g91_g28_z0(masked_text: str) -> Dict[str, Any]:
    lines = masked_text.splitlines()
    for i, line in enumerate(lines, start=1):
        z = extract_z_value(line)
        if line_has_token(line, "G91") and line_has_token(line, "G28") and (z is not None) and abs(z - 0.0) <= 1e-6:
            return mk_result("HOME", "回零檢查（G91 G28 Z0.0）", "安全回零", STATUS_OK, f"命中：第{i}行", line=i, col=1)

    for i, line in enumerate(lines):
        if line_has_token(line, "G91") and line_has_token(line, "G28"):
            for j in range(i, min(i + 4, len(lines))):
                z2 = extract_z_value(lines[j])
                if (z2 is not None) and abs(z2 - 0.0) <= 1e-6:
                    return mk_result("HOME", "回零檢查（G91 G28 Z0.0）", "安全回零", STATUS_OK, f"命中：第{i+1}行 + 第{j+1}行", line=i+1, col=1)

    return mk_result("HOME", "回零檢查（G91 G28 Z0.0）", "安全回零", STATUS_MISSING, "未找到同一行或相鄰行組合（G91 + G28 + Z0.0）")


# ============================
# 主軸正轉檢查（M03）
# ============================
def eval_spindle_m03(masked_text: str) -> Dict[str, Any]:
    m = find_gcode_token(masked_text, "M03")
    token_used = "M03"
    if not m:
        m = find_gcode_token(masked_text, "M3")
        token_used = "M3"
    if not m:
        return mk_result("M03", "主軸正轉檢查（M03）", "主軸", STATUS_MISSING, "未找到 M03（或 M3）")

    before = masked_text[:m.start(2)]
    line_no = before.count("\n") + 1
    return mk_result("M03", "主軸正轉檢查（M03）", "主軸", STATUS_OK, f"命中：{token_used}｜第{line_no}行", line=line_no, col=1)


# ============================
# Quick check：G碼 + T/H
# ============================
def eval_gcode_quick(masked_text: str, line_starts: List[int], tokens: List[str], need_t: bool, need_h: bool) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []

    for token in tokens:
        m = find_gcode_token(masked_text, token)
        if not m:
            results.append(mk_result(token, token, "G碼", STATUS_MISSING))
        else:
            idx = m.start(2)
            line_no, col_no = index_to_line_col(idx, line_starts)
            results.append(mk_result(token, token, "G碼", STATUS_OK, f"第{line_no}行，第{col_no}列", pos_idx=idx, line=line_no, col=col_no))

    if need_t:
        nums, first_idx = collect_addr_numbers(masked_text, "T")
        if not nums:
            results.append(mk_result("T", "T 號", "地址碼", STATUS_MISSING))
        else:
            ev = f"找到：{', '.join('T'+x for x in nums)}（共{len(nums)}個）"
            if first_idx is not None:
                line_no, col_no = index_to_line_col(first_idx, line_starts)
                ev += f"｜首次：第{line_no}行，第{col_no}列"
                results.append(mk_result("T", "T 號", "地址碼", STATUS_OK, ev, pos_idx=first_idx, line=line_no, col=col_no))
            else:
                results.append(mk_result("T", "T 號", "地址碼", STATUS_OK, ev))

    if need_h:
        nums, first_idx = collect_addr_numbers(masked_text, "H")
        if not nums:
            results.append(mk_result("H", "H 號", "地址碼", STATUS_MISSING))
        else:
            ev = f"找到：{', '.join('H'+x for x in nums)}（共{len(nums)}個）"
            if first_idx is not None:
                line_no, col_no = index_to_line_col(first_idx, line_starts)
                ev += f"｜首次：第{line_no}行，第{col_no}列"
                results.append(mk_result("H", "H 號", "地址碼", STATUS_OK, ev, pos_idx=first_idx, line=line_no, col=col_no))
            else:
                results.append(mk_result("H", "H 號", "地址碼", STATUS_OK, ev))

    return results


# ============================
# 另存修正版：移除 TOOL LIST 區塊
# ============================
TOOL_LIST_TITLE_RE = re.compile(r"\(\s*T\s*O\s*O\s*L\s*L\s*I\s*S\s*T\s*\)", re.IGNORECASE)

def _is_comment_line(s: str) -> bool:
    st = s.strip()
    return st.startswith("(") and st.endswith(")")

def _is_blank(s: str) -> bool:
    return s.strip() == ""

def _should_remove_following_spindle_line(line: str) -> bool:
    ml = mask_gcode_comments_keep_length(line)
    if not line_has_token(ml, "G00"):
        return False
    has_spindle = (re.search(r"(^|[^0-9A-Z])S[0-9]+(?=[^0-9]|$)", ml, re.IGNORECASE) is not None) or line_has_token(ml, "M03") or line_has_token(ml, "M3")
    has_xy = (re.search(r"(^|[^0-9A-Z])X[+-]?(?:\d+\.\d*|\d*\.\d+|\d+)(?=[^0-9]|$)", ml, re.IGNORECASE) is not None) or \
             (re.search(r"(^|[^0-9A-Z])Y[+-]?(?:\d+\.\d*|\d*\.\d+|\d+)(?=[^0-9]|$)", ml, re.IGNORECASE) is not None)
    return has_spindle and has_xy

def remove_tool_list_blocks(text: str) -> Tuple[str, int, int]:
    lines = text.splitlines(keepends=True)
    n = len(lines)
    remove = [False] * n
    blocks = 0
    removed_lines = 0

    i = 0
    while i < n:
        if TOOL_LIST_TITLE_RE.search(lines[i]):
            start = i
            j = i - 1
            while j >= 0 and (_is_comment_line(lines[j]) or _is_blank(lines[j])):
                start = j
                j -= 1

            end = i + 1
            k = i + 1
            while k < n and (_is_comment_line(lines[k]) or _is_blank(lines[k])):
                end = k + 1
                k += 1

            for idx in range(start, end):
                if not remove[idx]:
                    remove[idx] = True
                    removed_lines += 1

            blocks += 1

            if end < n and _should_remove_following_spindle_line(lines[end]):
                if not remove[end]:
                    remove[end] = True
                    removed_lines += 1
                end += 1

            i = end
            continue

        i += 1

    out = [lines[idx] for idx in range(n) if not remove[idx]]
    return "".join(out), blocks, removed_lines


# ============================
# 另存修正版：路徑刪除（保留重要碼）
# ============================
IMPORTANT_KEEP_RE = re.compile(
    r"(^|[^0-9A-Z])("
    r"G43|G54|G55|G56|G57|G58|G59|"
    r"T[0-9]+|H[0-9]+|"
    r"M0?6"
    r")(?=[^0-9]|$)",
    re.IGNORECASE
)

def compute_head_tail_window(masked_text: str) -> Tuple[int, int, Optional[int], Optional[int]]:
    """偵測『檔頭/檔尾』保留範圍（以 G90 且 Z < TRIM_END_Z_MIN 判定切削區間）。

    回傳：
      - head_end_line：檔頭保留到第幾行（含），1-based；可能為 0（代表不保留檔頭）
      - tail_start_line：檔尾從第幾行開始保留（含），1-based；可能為 n+1（代表不保留檔尾）
      - first_below_line：第一個命中切削區間（Z < TRIM_END_Z_MIN）的行號（1-based），找不到則為 None
      - retract_line：最後一次切削後，第一次回升到安全高度（G00 且 Z >= TRIM_END_Z_MIN）的行號（1-based），找不到則為 None
    """
    mask_lines = masked_text.splitlines(keepends=True)
    n = len(mask_lines)

    pos_mode: Optional[str] = None
    motion_mode: Optional[str] = None
    first_below: Optional[int] = None
    last_below: Optional[int] = None

    for i, ml in enumerate(mask_lines):
        if line_has_token(ml, "G90"):
            pos_mode = "G90"
        elif line_has_token(ml, "G91"):
            pos_mode = "G91"

        if line_has_token(ml, "G00"):
            motion_mode = "G00"
        elif line_has_token(ml, "G01") or line_has_token(ml, "G1"):
            motion_mode = "G01"
        elif line_has_token(ml, "G02") or line_has_token(ml, "G2"):
            motion_mode = "G02"
        elif line_has_token(ml, "G03") or line_has_token(ml, "G3"):
            motion_mode = "G03"

        z = extract_z_value(ml)
        if (pos_mode == "G90") and (z is not None) and (z < TRIM_END_Z_MIN - EPS):
            if not (line_has_token(ml, "G28") or line_has_token(ml, "G53")):
                if first_below is None:
                    first_below = i
                last_below = i

    # 未偵測到切削區間 → 保留全檔
    if first_below is None or last_below is None:
        return n, n + 1, None, None

    # 第二趟：找最後切削後的安全回升（G00 且 Z >= TRIM_END_Z_MIN）
    pos_mode = None
    motion_mode = None
    retract: Optional[int] = None

    for i, ml in enumerate(mask_lines):
        if line_has_token(ml, "G90"):
            pos_mode = "G90"
        elif line_has_token(ml, "G91"):
            pos_mode = "G91"

        if line_has_token(ml, "G00"):
            motion_mode = "G00"
        elif line_has_token(ml, "G01") or line_has_token(ml, "G1"):
            motion_mode = "G01"
        elif line_has_token(ml, "G02") or line_has_token(ml, "G2"):
            motion_mode = "G02"
        elif line_has_token(ml, "G03") or line_has_token(ml, "G3"):
            motion_mode = "G03"

        if i <= last_below:
            continue

        z = extract_z_value(ml)
        if (pos_mode == "G90") and (motion_mode == "G00") and (z is not None) and (z >= TRIM_END_Z_MIN - EPS):
            if not (line_has_token(ml, "G28") or line_has_token(ml, "G53")):
                retract = i
                break

    # 檔頭：保留到 first_below 前一行（可能為 0）
    head_end_line = max(first_below, 0)  # 1-based：first_below(0-based) → 行號 = first_below
    # 檔尾：若找到回升，從回升行開始；否則從最後切削行「下一行」開始
    if retract is not None:
        tail_start_line = retract + 1  # 1-based
    else:
        tail_start_line = min((last_below + 2), n + 1)  # 1-based，可能 = n+1（無檔尾）

    return head_end_line, tail_start_line, (first_below + 1), (retract + 1 if retract is not None else None)


def apply_scope_keep_head_tail(masked_text: str, head_end_line: int, tail_start_line: int) -> str:
    """把 masked_text 中間段落清空（改成空白），保留檔頭/檔尾，且維持字元長度不變，以維持 index↔行列對應。"""
    parts = masked_text.splitlines(keepends=True)
    out: List[str] = []
    for ln, part in enumerate(parts, start=1):
        if (ln <= head_end_line) or (ln >= tail_start_line):
            out.append(part)
            continue

        # 需保留行尾換行，內容改空白（長度不變）
        if part.endswith("\r\n"):
            core = part[:-2]
            out.append((" " * len(core)) + "\r\n")
        elif part.endswith("\n") or part.endswith("\r"):
            core = part[:-1]
            out.append((" " * len(core)) + part[-1])
        else:
            out.append(" " * len(part))
    return "".join(out)


def trim_toolpath_by_markers(original_text: str, masked_text: str) -> Tuple[str, str]:
    """路徑刪除：只保留檔頭/檔尾（刪除中間切削路徑）。

    以『G90 且 Z < TRIM_END_Z_MIN』視為進入切削區間：
      - 檔頭：保留到第一個切削行的前一行
      - 檔尾：保留自最後切削後第一次回升到安全高度（G00 且 Z >= TRIM_END_Z_MIN）那一行起
        若找不到回升，則自最後切削行的下一行起（避免保留低於安全高度的點位）
    """
    orig_lines = original_text.splitlines(keepends=True)
    n = len(orig_lines)

    head_end_line, tail_start_line, first_below_line, retract_line = compute_head_tail_window(masked_text)

    if first_below_line is None:
        return original_text, f"未偵測到切削區間（未找到 G90 且 Z < {TRIM_END_Z_MIN} 的段落）"

    # 轉 0-based slicing
    head_keep = max(head_end_line, 0)
    tail_start_idx = max(tail_start_line - 1, 0)

    # 避免重疊
    if head_keep >= tail_start_idx:
        return original_text, "偵測結果異常（檔頭/檔尾重疊），未進行刪除"

    kept_head = orig_lines[:head_keep]
    kept_tail = orig_lines[tail_start_idx:] if tail_start_idx < n else []
    removed = n - (len(kept_head) + len(kept_tail))

    new_text = "".join(kept_head + kept_tail)

    if retract_line is None:
        msg = (
            f"僅保留檔頭/檔尾：檔頭=1~{head_end_line}行，檔尾={tail_start_line}~{n}行｜"
            f"未找到安全回升（G00 且 Z >= {TRIM_END_Z_MIN}）。刪除行數={removed}"
        )
    else:
        msg = (
            f"僅保留檔頭/檔尾：檔頭=1~{head_end_line}行，檔尾={tail_start_line}~{n}行｜"
            f"安全回升起點=第{retract_line}行。刪除行數={removed}"
        )

    return new_text, msg


# ============================
# 拖拉 path 正規化
# ============================
BaseTk = TkinterDnD.Tk if DND_AVAILABLE else tk.Tk

def normalize_dnd_path(p: str) -> str:
    p = (p or "").strip()
    if p.startswith("{") and p.endswith("}"):
        p = p[1:-1]
    return p.strip().strip('"')


# ============================
# 歷程：載入/儲存/輸出 TXT
# ============================
def load_history() -> List[Dict[str, Any]]:
    try:
        if not os.path.exists(HISTORY_PATH):
            return []
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        recs = data.get("records", [])
        return recs if isinstance(recs, list) else []
    except Exception:
        return []

def save_history(records: List[Dict[str, Any]]) -> None:
    try:
        payload = {"version": 1, "records": records}
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def history_to_txt(records: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    lines.append("程式內容檢查工具｜操作歷程")
    lines.append(f"匯出時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"歷程檔位置：{HISTORY_PATH}")
    lines.append("=" * 120)

    for idx, r in enumerate(reversed(records), start=1):
        ts = r.get("ts", "")
        job = r.get("job_id", "")
        files = r.get("files", [])
        summary = r.get("summary", {})
        opts = r.get("options", {})
        issues = r.get("issues", [])

        lines.append(f"[{idx}] 時間：{ts}｜工號：{job}")
        lines.append(f"    檔案數：{len(files)}｜缺少：{summary.get('missing', 0)}｜錯誤：{summary.get('errors', 0)}｜總計：{summary.get('total', 0)}")
        lines.append("    檔案：")
        for f in files[:20]:
            lines.append(f"      - {f.get('name','')}  ({f.get('path','')})")
        if len(files) > 20:
            lines.append(f"      ...（其餘{len(files)-20}個）")

        lines.append("    設定：")
        for k in sorted(opts.keys()):
            lines.append(f"      - {k} = {opts[k]}")

        if issues:
            lines.append("    不通過清單（缺少/錯誤）：")
            for it in issues[:80]:
                lines.append(
                    f"      - [{it.get('status','')}] {it.get('file','')}｜{it.get('label','')}｜{it.get('type','')}｜{it.get('range','')}｜{it.get('evidence','')}"
                )
            if len(issues) > 80:
                lines.append(f"      ...（其餘{len(issues)-80}筆）")
        else:
            lines.append("    不通過清單：無")

        lines.append("-" * 120)

    return "\n".join(lines)


# ============================
# 視窗：問題清單（不通過）
# ============================
class IssuesWindow:
    def __init__(self, parent: "App"):
        self.parent = parent
        self.win = tk.Toplevel(parent)
        self.win.title("問題清單（不通過項目）")
        self.win.geometry("1280x560")
        self.win.configure(bg=BG)

        self.win.grid_rowconfigure(1, weight=1)
        self.win.grid_columnconfigure(0, weight=1)

        top = ttk.Frame(self.win, padding=(12, 10))
        top.grid(row=0, column=0, sticky="ew")
        top.grid_columnconfigure(0, weight=1)

        ttk.Label(top, text="只顯示：狀態=缺少 / 錯誤（雙擊可定位到主表）", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(top, text="重新整理", command=self.refresh).grid(row=0, column=1, sticky="e", padx=(8, 0))

        columns = ("file", "label", "type", "status", "range", "evidence", "raw")
        self.tree = ttk.Treeview(self.win, columns=columns, show="headings")
        self.tree.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

        self.tree.heading("file", text="檔案")
        self.tree.heading("label", text="項目")
        self.tree.heading("type", text="類型")
        self.tree.heading("status", text="狀態")
        self.tree.heading("range", text="範圍")
        self.tree.heading("evidence", text="位置/證據")
        self.tree.heading("raw", text="原始內容（該行）")

        self.tree.column("file", width=240, anchor="w", stretch=False)
        self.tree.column("label", width=260, anchor="w", stretch=False)
        self.tree.column("type", width=90, anchor="w", stretch=False)
        self.tree.column("status", width=80, anchor="w", stretch=False)
        self.tree.column("range", width=140, anchor="w", stretch=False)
        self.tree.column("evidence", width=420, anchor="w", stretch=True)
        self.tree.column("raw", width=760, anchor="w", stretch=True)

        vsb = ttk.Scrollbar(self.win, orient="vertical", style="Vertical.TScrollbar", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.grid(row=1, column=1, sticky="ns", pady=(0, 12), padx=(0, 12))

        hsb = ttk.Scrollbar(self.win, orient="horizontal", style="Horizontal.TScrollbar", command=self.tree.xview)
        self.tree.configure(xscrollcommand=hsb.set)
        hsb.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))

        self.tree.tag_configure("bad", background=DANGER_BG, foreground=DANGER_FG)

        self.tree.bind("<Double-1>", self._on_double_click)
        self.refresh()

    def refresh(self):
        for i in self.tree.get_children():
            self.tree.delete(i)

        self.tree._main_iid_map = {}

        for row in self.parent._issue_rows:
            iid = self.tree.insert(
                "",
                "end",
                values=(
                    row["file"],
                    row["label"],
                    row["type"],
                    row["status"],
                    row["range"],
                    row["evidence"],
                    row["raw"],
                ),
                tags=("bad",),
            )
            self.tree._main_iid_map[iid] = row.get("_main_iid")

    def _on_double_click(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        main_iid = getattr(self.tree, "_main_iid_map", {}).get(iid)
        if not main_iid:
            return
        self.parent.focus_main_row(main_iid)


# ============================
# 視窗：歷程記錄
# ============================
class HistoryWindow:
    def __init__(self, parent: "App"):
        self.parent = parent
        self.win = tk.Toplevel(parent)
        self.win.title("檢查歷程記錄")
        self.win.geometry("1400x680")
        self.win.configure(bg=BG)

        self.win.grid_rowconfigure(1, weight=1)
        self.win.grid_columnconfigure(0, weight=1)

        top = ttk.Frame(self.win, padding=(12, 10))
        top.grid(row=0, column=0, sticky="ew")
        top.grid_columnconfigure(0, weight=1)

        ttk.Label(top, text=f"歷程檔：{HISTORY_PATH}", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        btns = ttk.Frame(top)
        btns.grid(row=0, column=1, sticky="e")
        ttk.Button(btns, text="匯出TXT", command=self.export_txt).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(btns, text="清除歷程", command=self.clear_history).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(btns, text="重新整理", command=self.refresh).grid(row=0, column=2)

        mid = ttk.Frame(self.win, padding=(12, 0, 12, 12))
        mid.grid(row=1, column=0, sticky="nsew")
        mid.grid_rowconfigure(0, weight=1)
        mid.grid_columnconfigure(0, weight=1)
        mid.grid_columnconfigure(1, weight=1)

        columns = ("ts", "job", "files", "missing", "errors", "total")
        self.tree = ttk.Treeview(mid, columns=columns, show="headings")
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        self.tree.heading("ts", text="時間")
        self.tree.heading("job", text="工號")
        self.tree.heading("files", text="檔案數")
        self.tree.heading("missing", text="缺少")
        self.tree.heading("errors", text="錯誤")
        self.tree.heading("total", text="總計")

        self.tree.column("ts", width=170, anchor="w", stretch=False)
        self.tree.column("job", width=120, anchor="w", stretch=False)
        self.tree.column("files", width=80, anchor="center", stretch=False)
        self.tree.column("missing", width=80, anchor="center", stretch=False)
        self.tree.column("errors", width=80, anchor="center", stretch=False)
        self.tree.column("total", width=80, anchor="center", stretch=False)

        vsb = ttk.Scrollbar(mid, orient="vertical", style="Vertical.TScrollbar", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.grid(row=0, column=0, sticky="nse", padx=(0, 10))

        right = ttk.Labelframe(mid, text="內容預覽", padding=(10, 8))
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_rowconfigure(0, weight=1)
        right.grid_columnconfigure(0, weight=1)

        self.txt = tk.Text(right, bg=PANEL2, fg=FG, insertbackground=FG, wrap="word", relief="flat")
        self.txt.grid(row=0, column=0, sticky="nsew")

        self.txt_ysb = ttk.Scrollbar(right, orient="vertical", style="Vertical.TScrollbar", command=self.txt.yview)
        self.txt.configure(yscrollcommand=self.txt_ysb.set)
        self.txt_ysb.grid(row=0, column=1, sticky="ns")

        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        self.refresh()

    def refresh(self):
        for i in self.tree.get_children():
            self.tree.delete(i)

        self._iid_to_index = {}
        recs = self.parent.history_records

        # 最新的放最上面
        for idx, r in enumerate(reversed(recs)):
            s = r.get("summary", {})
            iid = self.tree.insert(
                "",
                "end",
                values=(
                    r.get("ts", ""),
                    r.get("job_id", ""),
                    len(r.get("files", [])),
                    s.get("missing", 0),
                    s.get("errors", 0),
                    s.get("total", 0),
                ),
            )
            # 反向 index：reversed -> 原本 index = len-1-idx
            self._iid_to_index[iid] = len(recs) - 1 - idx

        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", "請點選左側歷程項目查看詳細內容。")

    def on_select(self, _evt=None):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        real_index = self._iid_to_index.get(iid)
        if real_index is None:
            return
        r = self.parent.history_records[real_index]
        self.txt.delete("1.0", "end")

        lines: List[str] = []
        lines.append(f"時間：{r.get('ts','')}")
        lines.append(f"工號：{r.get('job_id','')}")
        lines.append("")
        lines.append("檔案：")
        for f in r.get("files", []):
            lines.append(f"  - {f.get('name','')}  ({f.get('path','')})")
        lines.append("")
        lines.append("設定：")
        for k in sorted(r.get("options", {}).keys()):
            lines.append(f"  - {k} = {r['options'][k]}")
        lines.append("")
        s = r.get("summary", {})
        lines.append(f"結果：缺少={s.get('missing',0)}｜錯誤={s.get('errors',0)}｜總計={s.get('total',0)}")
        issues = r.get("issues", [])
        lines.append("")
        if issues:
            lines.append("不通過清單（缺少/錯誤）：")
            for it in issues[:200]:
                lines.append(f"  - [{it.get('status','')}] {it.get('file','')}｜{it.get('label','')}｜{it.get('range','')}｜{it.get('evidence','')}")
            if len(issues) > 200:
                lines.append(f"  ...（其餘{len(issues)-200}筆）")
        else:
            lines.append("不通過清單：無")

        self.txt.insert("1.0", "\n".join(lines))

    def export_txt(self):
        if not self.parent.history_records:
            messagebox.showinfo("提示", "目前沒有歷程可匯出。")
            return
        path = filedialog.asksaveasfilename(
            title="匯出歷程 TXT",
            defaultextension=".txt",
            filetypes=[("文字檔", "*.txt"), ("所有檔案", "*")],
        )
        if not path:
            return
        try:
            content = history_to_txt(self.parent.history_records)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            messagebox.showinfo("完成", f"已匯出：\n{path}")
        except Exception as e:
            messagebox.showerror("錯誤", f"匯出失敗：{e}")

    def clear_history(self):
        if not self.parent.history_records:
            return
        if not messagebox.askyesno("確認", "確定要清除所有歷程嗎？此操作不可復原。"):
            return
        self.parent.history_records = []
        save_history(self.parent.history_records)
        self.refresh()
        messagebox.showinfo("完成", "已清除歷程。")


# ============================
# GUI 主程式
# ============================
class App(BaseTk):
    def __init__(self):
        super().__init__()
        self.title("程式內容檢查工具（批量 / G碼 / T / H）")
        self.geometry("1550x940")
        self.minsize(1280, 780)
        self.configure(bg=BG)

        # ---- 工號 ----
        self.job_id_var = tk.StringVar(value="")

        # ---- 檔案 ----
        self.file_paths: List[str] = []
        self.file_path_var = tk.StringVar()

        # ---- 勾選 ----
        self.chk_g54 = tk.BooleanVar(value=True)
        self.chk_g55 = tk.BooleanVar(value=False)
        self.chk_g56 = tk.BooleanVar(value=False)
        self.chk_g57 = tk.BooleanVar(value=False)
        self.chk_g58 = tk.BooleanVar(value=False)
        self.chk_g59 = tk.BooleanVar(value=False)
        self.chk_g43 = tk.BooleanVar(value=True)
        self.chk_tnum = tk.BooleanVar(value=True)
        self.chk_hnum = tk.BooleanVar(value=True)

        # 刪除路徑（另存修正版）
        self.chk_trim_path = tk.BooleanVar(value=False)

        # 安全高度
        self.chk_safety_enable = tk.BooleanVar(value=True)
        self.material_height_var = tk.StringVar(value="0.0000")

        # UI 文本
        self.t_summary_var = tk.StringVar(value="T: -")
        self.h_summary_var = tk.StringVar(value="H: -")
        self.file_info_var = tk.StringVar(value="尚未載入檔案")
        self.status_var = tk.StringVar(value="就緒")

        # 資料
        self._issue_rows: List[Dict[str, Any]] = []
        self._issues_win: Optional[IssuesWindow] = None
        self._history_win: Optional[HistoryWindow] = None

        # 歷程資料（跨次保留）
        self.history_records: List[Dict[str, Any]] = load_history()

        self._apply_style()
        self._build_ui()
        self.after(180, self._set_default_sash)

        if DND_AVAILABLE:
            try:
                self.drop_target_register(DND_FILES)
                self.dnd_bind("<<Drop>>", self.on_drop_file)
            except Exception:
                pass

    def _apply_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        base_font = ("Microsoft JhengHei UI", 10)
        bold_font = ("Microsoft JhengHei UI", 10, "bold")
        title_font = ("Microsoft JhengHei UI", 13, "bold")
        self.option_add("*Font", base_font)

        style.configure(".", background=BG, foreground=FG)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("Title.TLabel", background=BG, foreground=FG, font=title_font)

        style.configure("TLabelframe", background=PANEL, borderwidth=1, relief="solid")
        style.configure("TLabelframe.Label", background=PANEL, foreground=ACCENT, font=bold_font)

        style.configure("TEntry", fieldbackground=PANEL2, background=PANEL2, foreground=FG, insertcolor=FG)

        style.configure("TButton", padding=(10, 5), background="#7A7A7A", foreground="#101010", borderwidth=1)
        style.map("TButton",
                  background=[("active", "#9A9A9A"), ("pressed", "#B0B0B0")],
                  foreground=[("disabled", "#DDDDDD")])
        style.configure("Accent.TButton", background=ACCENT, foreground="#101010", padding=(10, 5))
        style.map("Accent.TButton",
                  background=[("active", "#7BE6FF"), ("pressed", "#35D2FF")])

        style.configure("TCheckbutton", background=PANEL, foreground=FG, padding=(6, 2))

        style.configure("Treeview",
                        background=PANEL2,
                        fieldbackground=PANEL2,
                        foreground=FG,
                        borderwidth=0,
                        rowheight=30)
        style.configure("Treeview.Heading",
                        background="#7A7A7A",
                        foreground="#101010",
                        font=bold_font,
                        relief="flat")
        style.map("Treeview.Heading", background=[("active", "#9A9A9A")])

        style.configure("Pill.TLabel", background="#7A7A7A", foreground="#101010", padding=(10, 4), relief="solid")

        style.configure("Vertical.TScrollbar",
                        troughcolor=SCROLL_TROUGH,
                        background=SCROLL_BG,
                        arrowcolor="#101010",
                        bordercolor=SCROLL_TROUGH,
                        width=22)
        style.map("Vertical.TScrollbar",
                  background=[("active", SCROLL_ACTIVE), ("pressed", SCROLL_PRESSED)])

        style.configure("Horizontal.TScrollbar",
                        troughcolor=SCROLL_TROUGH,
                        background=SCROLL_BG,
                        arrowcolor="#101010",
                        bordercolor=SCROLL_TROUGH,
                        width=18)
        style.map("Horizontal.TScrollbar",
                  background=[("active", SCROLL_ACTIVE), ("pressed", SCROLL_PRESSED)])

    def _build_ui(self):
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = ttk.Frame(self, padding=(14, 10))
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(1, weight=1)

        ttk.Label(header, text="程式內容檢查工具", style="Title.TLabel").grid(row=0, column=0, sticky="w")

        tool = ttk.Frame(header)
        tool.grid(row=0, column=2, sticky="e")
        ttk.Button(tool, text="開始檢查", style="Accent.TButton", command=self.run_check).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(tool, text="問題清單", command=self.open_issues_window).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(tool, text="歷程記錄", command=self.open_history_window).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(tool, text="清除結果", command=self.clear_results).grid(row=0, column=3)

        main = ttk.Frame(self, padding=(14, 0, 14, 14))
        main.grid(row=1, column=0, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)

        # ===== 檔案區 =====
        file_group = ttk.Labelframe(main, text="檔案（可批量）", padding=(12, 10))
        file_group.grid(row=0, column=0, sticky="ew")
        file_group.grid_columnconfigure(1, weight=1)

        ttk.Label(file_group, text="檔案：", background=PANEL, foreground=FG).grid(row=0, column=0, sticky="w")
        self.file_entry = ttk.Entry(file_group, textvariable=self.file_path_var)
        self.file_entry.grid(row=0, column=1, sticky="ew", padx=10)

        ttk.Button(file_group, text="瀏覽(多選)", command=self.browse_text_files).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(file_group, text="載入", command=self.load_first_file).grid(row=0, column=3)

        # 工號欄
        ttk.Label(file_group, text="工號：", background=PANEL, foreground=FG).grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.job_entry = ttk.Entry(file_group, textvariable=self.job_id_var, width=24)
        self.job_entry.grid(row=1, column=1, sticky="w", padx=10, pady=(10, 0))
        ttk.Label(file_group, text="（開檔後必填，將寫入檢查歷程）", style="Muted.TLabel").grid(row=1, column=1, sticky="w", padx=(220, 0), pady=(10, 0))

        hint = "可按「瀏覽(多選)」一次選多個檔案；也可拖拉多檔進視窗" if DND_AVAILABLE else "可按「瀏覽(多選)」一次選多個檔案"
        ttk.Label(file_group, text=hint, style="Muted.TLabel").grid(row=2, column=0, columnspan=4, sticky="w", pady=(10, 0))
        ttk.Label(file_group, textvariable=self.file_info_var, background=PANEL, foreground=MUTED).grid(row=3, column=0, columnspan=4, sticky="w", pady=(6, 0))

        if DND_AVAILABLE:
            try:
                self.file_entry.drop_target_register(DND_FILES)
                self.file_entry.dnd_bind("<<Drop>>", self.on_drop_file)
            except Exception:
                pass

        # ===== 科技感分隔：PanedWindow（只用最保險的選項，避免 TclError）=====
        self.paned = tk.PanedWindow(
            main,
            orient=tk.VERTICAL,
            bg=SASH_BG,
            bd=0,
            relief=tk.FLAT
        )
        self.paned.grid(row=1, column=0, sticky="nsew", pady=(12, 0))

        top_area = tk.Frame(self.paned, bg=BG)
        bottom_area = tk.Frame(self.paned, bg=BG)
        top_area.grid_columnconfigure(0, weight=1)

        bottom_area.grid_rowconfigure(0, weight=0)
        bottom_area.grid_rowconfigure(1, weight=1)
        bottom_area.grid_columnconfigure(0, weight=1)

        self.paned.add(top_area)
        self.paned.add(bottom_area)

        # ===== 安全設定 =====
        grp_safe = ttk.Labelframe(top_area, text="安全設定", padding=(12, 10))
        grp_safe.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        grp_safe.grid_columnconfigure(5, weight=1)

        ttk.Checkbutton(grp_safe, text="安全高度碰撞檢查", variable=self.chk_safety_enable).grid(row=0, column=0, sticky="w")
        ttk.Label(grp_safe, text="素材高度", background=PANEL, foreground=FG).grid(row=0, column=1, sticky="e", padx=(12, 8))
        ttk.Entry(grp_safe, textvariable=self.material_height_var, width=12).grid(row=0, column=2, sticky="w")
        ttk.Label(grp_safe, text="（可輸入 2.0 或 Z2.0）", background=PANEL, foreground=MUTED).grid(row=0, column=3, sticky="w", padx=(10, 0))
        ttk.Label(
            grp_safe,
            text="規則：Z值低於素材高度會導致撞機風險（僅比對『G00 G90 Z(正值)』；Z為負值不比對）",
            background=PANEL, foreground=MUTED
        ).grid(row=1, column=0, columnspan=6, sticky="w", pady=(10, 0))

        # ===== 工件座標系 =====
        grp_offset = ttk.Labelframe(top_area, text="工件座標系（G54 ~ G59）", padding=(12, 10))
        grp_offset.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        for c in range(6):
            grp_offset.grid_columnconfigure(c, weight=1)

        ttk.Checkbutton(grp_offset, text="G54", variable=self.chk_g54).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(grp_offset, text="G55", variable=self.chk_g55).grid(row=0, column=1, sticky="w")
        ttk.Checkbutton(grp_offset, text="G56", variable=self.chk_g56).grid(row=0, column=2, sticky="w")
        ttk.Checkbutton(grp_offset, text="G57", variable=self.chk_g57).grid(row=0, column=3, sticky="w")
        ttk.Checkbutton(grp_offset, text="G58", variable=self.chk_g58).grid(row=0, column=4, sticky="w")
        ttk.Checkbutton(grp_offset, text="G59", variable=self.chk_g59).grid(row=0, column=5, sticky="w")

        ttk.Button(grp_offset, text="全選", command=self.select_all_offsets).grid(row=1, column=4, sticky="e", pady=(10, 0))
        ttk.Button(grp_offset, text="全不選", command=self.clear_all_offsets).grid(row=1, column=5, sticky="w", pady=(10, 0))

        # ===== 刀長補正 / 刀具資訊 =====
        grp_tool = ttk.Labelframe(top_area, text="刀長補正 / 刀具資訊", padding=(12, 10))
        grp_tool.grid(row=2, column=0, sticky="ew")
        grp_tool.grid_columnconfigure(3, weight=1)

        ttk.Checkbutton(grp_tool, text="檢查 G43", variable=self.chk_g43).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(grp_tool, text="顯示 T 號", variable=self.chk_tnum).grid(row=0, column=1, sticky="w", padx=(12, 0))
        ttk.Checkbutton(grp_tool, text="顯示 H 號", variable=self.chk_hnum).grid(row=0, column=2, sticky="w", padx=(12, 0))
        ttk.Label(grp_tool, text="（確認刀具長度補正與工件座標）", background=PANEL, foreground=MUTED).grid(row=0, column=3, sticky="e")

        # ===== 路徑刪除（另存修正版）=====
        grp_trim = ttk.Labelframe(bottom_area, text="路徑刪除（另存修正版）", padding=(12, 8))
        grp_trim.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        grp_trim.grid_columnconfigure(0, weight=0)
        grp_trim.grid_columnconfigure(1, weight=1)
        grp_trim.grid_columnconfigure(2, weight=0)

        ttk.Checkbutton(grp_trim, text="啟用路徑刪除開關", variable=self.chk_trim_path).grid(row=0, column=0, sticky="w")

        # ✅ 需求 1：開關旁備註
        ttk.Label(
            grp_trim,
            text="規則備註：只保留檔頭/檔尾（刪除中間切削路徑），用於結刀/結程確認安全位置",
            style="Muted.TLabel"
        ).grid(row=0, column=1, sticky="w", padx=(12, 0))

        ttk.Button(grp_trim, text="另存修正版程式...", command=self.save_processed_program).grid(row=0, column=2, sticky="e")

        # ===== 檢查結果 =====
        res_group = ttk.Labelframe(bottom_area, text="檢查結果", padding=(12, 10))
        res_group.grid(row=1, column=0, sticky="nsew")
        res_group.grid_rowconfigure(2, weight=1)
        res_group.grid_columnconfigure(0, weight=1)

        ttk.Label(res_group, textvariable=self.status_var, background=PANEL, foreground=MUTED).grid(row=0, column=0, sticky="w", pady=(0, 8))

        pill_bar = ttk.Frame(res_group)
        pill_bar.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        pill_bar.grid_columnconfigure(1, weight=1)

        ttk.Label(pill_bar, text="T 號：", background=PANEL, foreground=FG).grid(row=0, column=0, sticky="w")
        ttk.Label(pill_bar, textvariable=self.t_summary_var, style="Pill.TLabel").grid(row=0, column=1, sticky="w", padx=(8, 18))
        ttk.Label(pill_bar, text="H 號：", background=PANEL, foreground=FG).grid(row=0, column=2, sticky="w")
        ttk.Label(pill_bar, textvariable=self.h_summary_var, style="Pill.TLabel").grid(row=0, column=3, sticky="w", padx=(8, 0))

        columns = ("file", "label", "type", "status", "range", "evidence", "raw")
        self.tree = ttk.Treeview(res_group, columns=columns, show="headings")
        self.tree.grid(row=2, column=0, sticky="nsew")

        self.tree.heading("file", text="檔案")
        self.tree.heading("label", text="項目")
        self.tree.heading("type", text="類型")
        self.tree.heading("status", text="狀態")
        self.tree.heading("range", text="範圍")
        self.tree.heading("evidence", text="位置/證據")
        self.tree.heading("raw", text="原始內容（該行）")

        self.tree.column("file", width=230, anchor="w", stretch=False)
        self.tree.column("label", width=260, anchor="w", stretch=False)
        self.tree.column("type", width=90, anchor="w", stretch=False)
        self.tree.column("status", width=80, anchor="w", stretch=False)
        self.tree.column("range", width=140, anchor="w", stretch=False)
        self.tree.column("evidence", width=520, anchor="w", stretch=True)
        self.tree.column("raw", width=820, anchor="w", stretch=True)

        vsb = ttk.Scrollbar(res_group, orient="vertical", style="Vertical.TScrollbar", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.grid(row=2, column=1, sticky="ns", padx=(10, 0))

        hsb = ttk.Scrollbar(res_group, orient="horizontal", style="Horizontal.TScrollbar", command=self.tree.xview)
        self.tree.configure(xscrollcommand=hsb.set)
        hsb.grid(row=3, column=0, sticky="ew", pady=(10, 0))

        self.tree.tag_configure("even", background=ROW_EVEN, foreground=FG)
        self.tree.tag_configure("odd", background=ROW_ODD, foreground=FG)
        self.tree.tag_configure("ok", foreground=OK_FG)
        self.tree.tag_configure("bad", background=DANGER_BG, foreground=DANGER_FG)

    def _set_default_sash(self):
        try:
            self.update_idletasks()
            total_h = self.paned.winfo_height()
            total_w = self.paned.winfo_width()
            if total_h > 0 and total_w > 0:
                y = int(total_h * 0.48)
                x = int(total_w * 0.50)
                # 如果此 Tk 版本不支援 sash_place，忽略即可
                if hasattr(self.paned, "sash_place"):
                    self.paned.sash_place(0, x, y)
        except Exception:
            pass

    # ===== 工號：開檔後必填 =====
    def ensure_job_id(self) -> bool:
        job = (self.job_id_var.get() or "").strip()
        if job:
            return True
        # 強制彈窗輸入
        job = simpledialog.askstring("輸入工號", "請輸入工號（將記錄檢查歷程）：", parent=self)
        if job is None:
            messagebox.showerror("錯誤", "未輸入工號，無法進行檢查/記錄。")
            return False
        job = job.strip()
        if not job:
            messagebox.showerror("錯誤", "工號不可空白。")
            return False
        self.job_id_var.set(job)
        return True

    # ===== 檔案選擇 =====
    def browse_text_files(self):
        paths = filedialog.askopenfilenames(title="選擇程式檔案（可多選）", filetypes=[("所有檔案", "*")])
        if paths:
            self.file_paths = list(paths)
            self._update_file_display()
            # 開檔後要求工號
            self.ensure_job_id()

    def on_drop_file(self, event):
        try:
            paths = self.tk.splitlist(event.data)
            if not paths:
                return
            normalized = []
            for p in paths:
                p2 = normalize_dnd_path(p)
                if p2 and os.path.exists(p2):
                    normalized.append(p2)
            if normalized:
                self.file_paths = normalized
                self._update_file_display()
                # 開檔後要求工號
                if self.ensure_job_id():
                    self.load_first_file()
        except Exception:
            pass

    def _update_file_display(self):
        if not self.file_paths:
            self.file_path_var.set("")
            self.file_info_var.set("尚未載入檔案")
            return
        if len(self.file_paths) == 1:
            self.file_path_var.set(self.file_paths[0])
        else:
            self.file_path_var.set(f"已選擇 {len(self.file_paths)} 個檔案（第一個：{os.path.basename(self.file_paths[0])}）")
        self.file_info_var.set(f"已選擇檔案數量：{len(self.file_paths)}")

    def load_first_file(self):
        if not self.file_paths:
            messagebox.showerror("錯誤", "請先選擇檔案（可多選）。")
            return
        # 開檔後要求工號
        if not self.ensure_job_id():
            return

        path = self.file_paths[0]
        try:
            text, enc = read_text_with_fallback(path)
            lines = text.splitlines()
            size_kb = os.path.getsize(path) / 1024.0
            self.file_info_var.set(
                f"已載入（顯示用）：{os.path.basename(path)}｜行數：{len(lines)}｜大小：{size_kb:.1f} KB｜編碼：{enc}｜（共選{len(self.file_paths)}個檔案）"
            )
            self.status_var.set("已選擇檔案，請按「開始檢查」。")
        except Exception as e:
            messagebox.showerror("錯誤", str(e))

    # ===== 結果區 =====
    def clear_results(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        self._issue_rows = []
        self.status_var.set("已清除結果。")
        self.t_summary_var.set("T: -")
        self.h_summary_var.set("H: -")
        if self._issues_win:
            self._issues_win.refresh()

    def select_all_offsets(self):
        self.chk_g54.set(True)
        self.chk_g55.set(True)
        self.chk_g56.set(True)
        self.chk_g57.set(True)
        self.chk_g58.set(True)
        self.chk_g59.set(True)

    def clear_all_offsets(self):
        self.chk_g54.set(False)
        self.chk_g55.set(False)
        self.chk_g56.set(False)
        self.chk_g57.set(False)
        self.chk_g58.set(False)
        self.chk_g59.set(False)

    def _get_selected_gcode_tokens(self) -> Tuple[List[str], bool, bool]:
        tokens: List[str] = []
        if self.chk_g54.get(): tokens.append("G54")
        if self.chk_g55.get(): tokens.append("G55")
        if self.chk_g56.get(): tokens.append("G56")
        if self.chk_g57.get(): tokens.append("G57")
        if self.chk_g58.get(): tokens.append("G58")
        if self.chk_g59.get(): tokens.append("G59")
        if self.chk_g43.get(): tokens.append("G43")
        return tokens, self.chk_tnum.get(), self.chk_hnum.get()

    def open_issues_window(self):
        if self._issues_win is None or not self._issues_win.win.winfo_exists():
            self._issues_win = IssuesWindow(self)
        else:
            self._issues_win.win.lift()
            self._issues_win.refresh()

    def open_history_window(self):
        if self._history_win is None or not self._history_win.win.winfo_exists():
            self._history_win = HistoryWindow(self)
        else:
            self._history_win.win.lift()
            self._history_win.refresh()

    def focus_main_row(self, main_iid: str):
        try:
            self.tree.selection_set(main_iid)
            self.tree.focus(main_iid)
            self.tree.see(main_iid)
            self.lift()
        except Exception:
            pass

    # ===== 另存修正版（批量/單檔）=====
    def save_processed_program(self):
        if not self.file_paths:
            messagebox.showerror("錯誤", "請先選擇檔案（可多選）。")
            return
        if not self.chk_trim_path.get():
            messagebox.showinfo("提示", "請先勾選「啟用路徑刪除開關」再另存。")
            return

        if len(self.file_paths) >= 2:
            out_dir = filedialog.askdirectory(title="選擇輸出資料夾（批量另存修正版）")
            if not out_dir:
                return
            ok_cnt, fail = self._batch_save_to_dir(out_dir)
            msg = f"批量完成：成功 {ok_cnt} / {len(self.file_paths)}"
            if fail:
                msg += "\n\n失敗清單：\n" + "\n".join(fail[:12]) + ("" if len(fail) <= 12 else f"\n...（其餘{len(fail)-12}筆）")
            messagebox.showinfo("完成", msg)
            return

        in_path = self.file_paths[0]
        base = os.path.basename(in_path)
        root, ext = os.path.splitext(base)
        if not ext:
            ext = ".nc"
        default_name = f"{root}_processed{ext}"

        save_path = filedialog.asksaveasfilename(
            title="另存修正版程式",
            initialfile=default_name,
            defaultextension=ext,
            filetypes=[("所有檔案", "*")],
        )
        if not save_path:
            return

        try:
            text, _enc = read_text_with_fallback(in_path)
            masked = mask_gcode_comments_keep_length(text)
            trimmed_text, trim_msg = trim_toolpath_by_markers(text, masked)
            cleaned_text, _, _ = remove_tool_list_blocks(trimmed_text)

            with open(save_path, "w", encoding="utf-8", newline="") as f:
                f.write(cleaned_text)

            messagebox.showinfo("完成", f"已另存：\n{save_path}\n\n{trim_msg}")
        except Exception as e:
            messagebox.showerror("錯誤", f"另存失敗：{e}")

    def _batch_save_to_dir(self, out_dir: str) -> Tuple[int, List[str]]:
        ok_cnt = 0
        fail: List[str] = []

        for path in self.file_paths:
            try:
                text, _enc = read_text_with_fallback(path)
                masked = mask_gcode_comments_keep_length(text)

                trimmed_text, _trim_msg = trim_toolpath_by_markers(text, masked)
                cleaned_text, _, _ = remove_tool_list_blocks(trimmed_text)

                base = os.path.basename(path)
                root, ext = os.path.splitext(base)
                if not ext:
                    ext = ".nc"
                out_name = f"{root}_processed{ext}"
                out_path = os.path.join(out_dir, out_name)

                with open(out_path, "w", encoding="utf-8", newline="") as f:
                    f.write(cleaned_text)

                ok_cnt += 1
            except Exception as e:
                fail.append(f"{os.path.basename(path)}：{e}")

        return ok_cnt, fail

    # ===== 開始檢查 =====
    def run_check(self):
        if not self.file_paths:
            messagebox.showerror("錯誤", "請先選擇檔案（可多選）。")
            return

        # 必填工號
        if not self.ensure_job_id():
            return

        for i in self.tree.get_children():
            self.tree.delete(i)
        self._issue_rows = []

        material_h: Optional[float] = None
        if self.chk_safety_enable.get():
            material_h = try_parse_float(self.material_height_var.get())
            if material_h is None:
                messagebox.showerror("撞機警報", "素材高度欄位必須是數字（可輸入 2.0 或 Z2.0）。")
                return

        tokens, need_t, need_h = self._get_selected_gcode_tokens()

        total_missing = 0
        total_errors = 0
        total_items = 0
        warned_once = False

        for fidx, path in enumerate(self.file_paths):
            filename = os.path.basename(path)

            try:
                text, _enc = read_text_with_fallback(path)
            except Exception as e:
                r = mk_result("READ", "讀檔", "系統", STATUS_ERROR, f"讀取失敗：{e}")
                self._append_row(filename, r)
                total_errors += 1
                total_items += 1
                continue

            masked_full = mask_gcode_comments_keep_length(text)
            head_end_line, tail_start_line, _fb, _rt = compute_head_tail_window(masked_full)
            masked = apply_scope_keep_head_tail(masked_full, head_end_line, tail_start_line)
            line_starts = build_line_starts(text)
            lines_plain = text.splitlines()

            results: List[Dict[str, Any]] = []

            # 1) 安全高度碰撞檢查（素材 vs G00 G90 Z正值）
            if self.chk_safety_enable.get() and material_h is not None:
                safety = eval_safety_g00_g90_z_vs_material(masked, line_starts, material_h)
                results.append(safety)
                if (not warned_once) and safety["status"] == STATUS_ERROR:
                    warned_once = True
                    messagebox.showerror(
                        "撞機警報",
                        f"檔案：{filename}\n{safety['evidence']}\n\n例：G00 G90 Z3.0、素材=2.0 → 素材高度 < Z → 觸發警報"
                    )

            # 2) 宣告碼（G17 G40 G49 G80 G90）
            results.append(eval_declaration_line(masked))

            # 3) 回零（G91 G28 Z0.0）
            results.append(eval_home_return_g91_g28_z0(masked))

            # 4) M03
            results.append(eval_spindle_m03(masked))

            # 5) 勾選的 G碼 + T/H
            results += eval_gcode_quick(masked, line_starts, tokens, need_t, need_h)

            # 顯示第一個檔案的 T/H 總結
            if fidx == 0:
                if need_t:
                    t_nums, _ = collect_addr_numbers(masked, "T")
                    self.t_summary_var.set("T: " + (", ".join("T" + n for n in t_nums) if t_nums else "(缺少)"))
                else:
                    self.t_summary_var.set("T:（未勾選）")

                if need_h:
                    h_nums, _ = collect_addr_numbers(masked, "H")
                    self.h_summary_var.set("H: " + (", ".join("H" + n for n in h_nums) if h_nums else "(缺少)"))
                else:
                    self.h_summary_var.set("H:（未勾選）")

            for r in results:
                if r["status"] == STATUS_MISSING:
                    total_missing += 1
                elif r["status"] == STATUS_ERROR:
                    total_errors += 1
                total_items += 1
                self._append_row(filename, r, lines_plain)

        self.status_var.set(f"完成：缺少={total_missing}｜錯誤={total_errors}｜總計={total_items}｜檔案數={len(self.file_paths)}")

        # 自動開問題清單
        if self._issue_rows:
            self.open_issues_window()
        else:
            if self._issues_win:
                self._issues_win.refresh()

        # ✅ 需求 2：記錄工號 + 檢查歷程（跨次保留，且可匯出TXT）
        self._append_history_record(
            job_id=(self.job_id_var.get() or "").strip(),
            missing=total_missing,
            errors=total_errors,
            total=total_items
        )

        # 若歷程視窗開著就更新
        if self._history_win and self._history_win.win.winfo_exists():
            self._history_win.refresh()

    def _append_row(self, filename: str, r: Dict[str, Any], lines_plain: Optional[List[str]] = None):
        idx = len(self.tree.get_children())
        base_tag = "even" if idx % 2 == 0 else "odd"
        status_tag = "ok" if r["status"] == STATUS_OK else "bad"
        tags = [base_tag, status_tag]  # status_tag 放最後 → 會覆蓋背景色

        line_no = r.get("line") or 0
        if lines_plain and line_no > 0:
            range_text = make_range_text(lines_plain, line_no, radius=2)
            raw_line = lines_plain[line_no - 1] if 1 <= line_no <= len(lines_plain) else ""
        else:
            range_text, raw_line = "", ""

        main_iid = self.tree.insert(
            "",
            "end",
            values=(
                filename,
                r.get("label", ""),
                r.get("type", ""),
                r.get("status", ""),
                range_text,
                r.get("evidence", ""),
                raw_line.rstrip("\n"),
            ),
            tags=tags,
        )

        if r.get("status") in (STATUS_MISSING, STATUS_ERROR):
            self._issue_rows.append({
                "file": filename,
                "label": r.get("label", ""),
                "type": r.get("type", ""),
                "status": r.get("status", ""),
                "range": range_text,
                "evidence": r.get("evidence", ""),
                "raw": raw_line.rstrip("\n"),
                "_main_iid": main_iid
            })

    def _append_history_record(self, job_id: str, missing: int, errors: int, total: int):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        files = [{"name": os.path.basename(p), "path": p} for p in self.file_paths]

        options = {
            "安全高度碰撞檢查": bool(self.chk_safety_enable.get()),
            "素材高度": (self.material_height_var.get() or "").strip(),
            "工件座標系_G54": bool(self.chk_g54.get()),
            "工件座標系_G55": bool(self.chk_g55.get()),
            "工件座標系_G56": bool(self.chk_g56.get()),
            "工件座標系_G57": bool(self.chk_g57.get()),
            "工件座標系_G58": bool(self.chk_g58.get()),
            "工件座標系_G59": bool(self.chk_g59.get()),
            "檢查_G43": bool(self.chk_g43.get()),
            "顯示_T號": bool(self.chk_tnum.get()),
            "顯示_H號": bool(self.chk_hnum.get()),
            "啟用路徑刪除開關": bool(self.chk_trim_path.get()),
        }

        # 只把不通過項目存入歷程（避免爆大）
        issues = []
        for row in self._issue_rows[:400]:
            issues.append({
                "file": row.get("file", ""),
                "label": row.get("label", ""),
                "type": row.get("type", ""),
                "status": row.get("status", ""),
                "range": row.get("range", ""),
                "evidence": row.get("evidence", ""),
                "raw": row.get("raw", ""),
            })

        rec = {
            "ts": ts,
            "job_id": job_id,
            "files": files,
            "options": options,
            "summary": {"missing": missing, "errors": errors, "total": total},
            "issues": issues,
        }

        self.history_records.append(rec)

        # 控制歷程上限（避免無限成長）
        if len(self.history_records) > 2000:
            self.history_records = self.history_records[-2000:]

        save_history(self.history_records)

    # ===== 工件座標系快捷 =====
    def select_all_offsets(self):
        self.chk_g54.set(True)
        self.chk_g55.set(True)
        self.chk_g56.set(True)
        self.chk_g57.set(True)
        self.chk_g58.set(True)
        self.chk_g59.set(True)

    def clear_all_offsets(self):
        self.chk_g54.set(False)
        self.chk_g55.set(False)
        self.chk_g56.set(False)
        self.chk_g57.set(False)
        self.chk_g58.set(False)
        self.chk_g59.set(False)


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
