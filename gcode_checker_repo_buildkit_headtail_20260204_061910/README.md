# GCodeChecker

GitHub Actions 在 Windows 上打包單一 EXE（Windows 不用裝 Python 也能直接開）。

## 路徑刪除（另存修正版）規則
- 只保留「檔頭 + 檔尾」，中間切削路徑全部刪除，避免輸出內容過於雜亂。
- 檔頭/檔尾偵測依據：G90 且 Z < 10.0 視為進入切削區間；最後一次切削後回升到 G00 且 Z >= 10.0 視為回到安全高度。

## 產出 Windows EXE
1. 把整個專案 push 到你的 GitHub repo
2. GitHub → Actions → **Build Windows EXE**
3. 點 **Run workflow** → 再按一次 **Run workflow**
4. 跑完（綠色 ✅）→ 進入該次 run → 下方 **Artifacts**
5. 下載 **gcode_checker_windows_exe** → 解壓 → 執行 `GCodeChecker.exe`
