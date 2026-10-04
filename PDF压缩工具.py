#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import io
from pathlib import Path
import threading
import queue
import sys

# GUI libs (import later if running GUI)
import argparse

try:
    import fitz  # PyMuPDF
except Exception as e:
    raise SystemExit("缺少依赖 PyMuPDF，请运行: pip install pymupdf") from e

try:
    from PIL import Image
except Exception as e:
    raise SystemExit("缺少依赖 Pillow，请运行: pip install pillow") from e

# -------------------------
# 图像处理与 PDF 转换函数
# -------------------------
def pil_downscale(img: Image.Image, scale_percent: int) -> Image.Image:
    scale_percent = max(10, min(100, int(scale_percent)))
    if scale_percent == 100:
        return img
    new_w = max(1, int(img.width * scale_percent / 100))
    new_h = max(1, int(img.height * scale_percent / 100))
    return img.resize((new_w, new_h), Image.LANCZOS)


def convert_pdf(in_pdf: Path,
                out_pdf: Path,
                mode: str = "gray",
                threshold: int = 180,
                dpi: int = 200,
                use_jpeg: bool = True,
                jpeg_quality: int = 75,
                downscale_percent: int = 100,
                progress_cb=None):
    """
    将单个 PDF 转换并保存为 out_pdf。

    mode: "gray" (灰度压缩), "bw" (二值化黑白), "color" (保留颜色，仅压缩)
    """
    if mode not in ("gray", "bw", "color"):
        raise ValueError("mode 必须是 'gray'/'bw'/'color'")

    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    with fitz.open(in_pdf) as src:
        out = fitz.open()
        total = len(src)
        for idx, page in enumerate(src, start=1):
            if progress_cb:
                progress_cb(f"{in_pdf.name} - 第 {idx}/{total} 页…")

            if mode == "bw":
                # 渲染为灰度后再二值化
                pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csGRAY, alpha=False)
                img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
                if downscale_percent != 100:
                    img = pil_downscale(img, downscale_percent)
                bw_img = img.point(lambda p: 255 if p >= threshold else 0, mode="1")
                buf = io.BytesIO()
                # 使用 PNG 保存二值图（体积通常小且无损）
                bw_img.save(buf, format="PNG", optimize=True)
                img_bytes = buf.getvalue()
                buf.close()
                page_width_pts = (bw_img.width / dpi) * 72.0
                page_height_pts = (bw_img.height / dpi) * 72.0
                rect = fitz.Rect(0, 0, page_width_pts, page_height_pts)
                new_page = out.new_page(width=rect.width, height=rect.height)
                new_page.insert_image(rect, stream=img_bytes)

            else:
                # gray 或 color
                if mode == "gray":
                    pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csGRAY, alpha=False)
                    img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
                else:
                    pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=False)
                    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

                if downscale_percent != 100:
                    img = pil_downscale(img, downscale_percent)

                buf = io.BytesIO()
                if use_jpeg:
                    q = max(10, min(95, int(jpeg_quality)))
                    # JPEG 要求 RGB 或 L 模式
                    save_img = img.convert("RGB") if img.mode not in ("RGB", "L") else img
                    if save_img.mode == "L":
                        # Pillow 在灰度 JPEG 保存时需要 'L'
                        save_img.save(buf, format="JPEG", quality=q, optimize=True, progressive=True)
                    else:
                        save_img.save(buf, format="JPEG", quality=q, optimize=True, progressive=True)
                    img_bytes = buf.getvalue()
                    buf.close()
                    page_width_pts = (img.width / dpi) * 72.0
                    page_height_pts = (img.height / dpi) * 72.0
                else:
                    save_img = img.convert("L") if img.mode == "L" else img
                    save_img.save(buf, format="PNG", optimize=True)
                    img_bytes = buf.getvalue()
                    buf.close()
                    page_width_pts = (img.width / dpi) * 72.0
                    page_height_pts = (img.height / dpi) * 72.0

                rect = fitz.Rect(0, 0, page_width_pts, page_height_pts)
                new_page = out.new_page(width=rect.width, height=rect.height)
                new_page.insert_image(rect, stream=img_bytes)

        out.set_metadata(src.metadata or {})
        out.save(out_pdf, deflate=True, garbage=4)
        out.close()


# -------------------------
# 命令行批处理逻辑
# -------------------------
def batch_process(inputs: list[Path],
                  out_dir: Path|None,
                  suffix: str = "_cmp",
                  mode: str = "gray",
                  threshold: int = 180,
                  dpi: int = 200,
                  use_jpeg: bool = True,
                  jpeg_quality: int = 75,
                  downscale_percent: int = 100):
    pdfs = []
    for p in inputs:
        if p.is_file() and p.suffix.lower() == ".pdf":
            pdfs.append(p)
        elif p.is_dir():
            for f in p.rglob("*.pdf"):
                pdfs.append(f)
    if not pdfs:
        print("未找到任何 PDF。")
        return

    total = len(pdfs)
    ok = 0
    for idx, pdf in enumerate(pdfs, start=1):
        try:
            if out_dir:
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path = out_dir / (pdf.stem + suffix + ".pdf")
            else:
                out_path = pdf.with_name(pdf.stem + suffix + pdf.suffix)
            print(f"[{idx}/{total}] 处理：{pdf} -> {out_path}")
            convert_pdf(pdf, out_path, mode=mode, threshold=threshold, dpi=dpi,
                        use_jpeg=use_jpeg, jpeg_quality=jpeg_quality, downscale_percent=downscale_percent,
                        progress_cb=lambda s: print("   ", s))
            ok += 1
        except Exception as e:
            print(f"[错误] {pdf}: {e}")
    print(f"完成：{ok}/{total} 个文件成功。")


# -------------------------
# GUI（Tkinter）实现
# -------------------------
def run_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title("PDF压缩工具")
            self.geometry("820x640")
            self.minsize(640, 480)

            self.queue = queue.Queue()
            self.worker = None
            self.stop_flag = threading.Event()

            self._build_layout()
            self._build_scroll_content()
            self.poll_queue()

        def _build_layout(self):
            self.rowconfigure(0, weight=1)
            self.rowconfigure(1, weight=0)
            self.columnconfigure(0, weight=1)

            container = ttk.Frame(self)
            container.grid(row=0, column=0, sticky="nsew")
            container.rowconfigure(0, weight=1)
            container.columnconfigure(0, weight=1)

            self.canvas = tk.Canvas(container, borderwidth=0, highlightthickness=0)
            vsb = ttk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
            self.canvas.configure(yscrollcommand=vsb.set)
            self.canvas.grid(row=0, column=0, sticky="nsew")
            vsb.grid(row=0, column=1, sticky="ns")

            self.scroll_frame = ttk.Frame(self.canvas)
            self.scroll_window = self.canvas.create_window((0, 0), window=self.scroll_frame, anchor="nw")

            def _on_configure(event):
                self.canvas.configure(scrollregion=self.canvas.bbox("all"))
                self.canvas.itemconfig(self.scroll_window, width=self.canvas.winfo_width())

            self.scroll_frame.bind("<Configure>", _on_configure)
            self.canvas.bind("<Configure>", _on_configure)
            self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
            self.canvas.bind_all("<Button-4>", self._on_mousewheel)
            self.canvas.bind_all("<Button-5>", self._on_mousewheel)

            self.action_bar = ttk.Frame(self, padding=(10, 8))
            self.action_bar.grid(row=1, column=0, sticky="ew")
            self.action_bar.columnconfigure(0, weight=1)

            self.start_btn = ttk.Button(self.action_bar, text="开始转换", command=self.start)
            self.start_btn.grid(row=0, column=0, sticky="w")
            self.stop_btn = ttk.Button(self.action_bar, text="停止", command=self.stop, state="disabled")
            self.stop_btn.grid(row=0, column=1, padx=(8, 0), sticky="w")

            self.status_text = tk.StringVar(value="待机")
            ttk.Label(self.action_bar, textvariable=self.status_text).grid(row=0, column=2, padx=12, sticky="e")
            self.action_bar.columnconfigure(2, weight=1)

        def _on_mousewheel(self, event):
            if event.num == 5 or event.delta < 0:
                self.canvas.yview_scroll(1, "units")
            elif event.num == 4 or event.delta > 0:
                self.canvas.yview_scroll(-1, "units")

        def _build_scroll_content(self):
            pad = {'padx': 10, 'pady': 8}

            frm_in = ttk.LabelFrame(self.scroll_frame, text="输入（文件或文件夹）")
            frm_in.grid(row=0, column=0, sticky="nsew", **pad)
            frm_in.columnconfigure(0, weight=1)
            frm_in.rowconfigure(0, weight=1)

            self.listbox = tk.Listbox(frm_in, height=6, selectmode=tk.EXTENDED)
            self.listbox.grid(row=0, column=0, sticky="nsew", padx=(10, 0), pady=10)

            sb = ttk.Scrollbar(frm_in, orient="vertical", command=self.listbox.yview)
            sb.grid(row=0, column=1, sticky="ns", pady=10)
            self.listbox.configure(yscrollcommand=sb.set)

            btns = ttk.Frame(frm_in)
            btns.grid(row=0, column=2, sticky="ns", padx=10, pady=10)
            ttk.Button(btns, text="添加 PDF 文件", command=self.add_files).pack(fill="x", pady=4)
            ttk.Button(btns, text="添加文件夹", command=self.add_folder).pack(fill="x", pady=4)
            ttk.Button(btns, text="移除选中", command=self.remove_selected).pack(fill="x", pady=4)
            ttk.Button(btns, text="清空列表", command=self.clear_all).pack(fill="x", pady=4)

            frm_opt = ttk.LabelFrame(self.scroll_frame, text="基本选项")
            frm_opt.grid(row=1, column=0, sticky="nsew", **pad)

            self.mode_var = tk.StringVar(value="gray")
            mode_row = ttk.Frame(frm_opt); mode_row.pack(fill="x", pady=6, padx=10)
            ttk.Label(mode_row, text="模式：").pack(side="left")
            ttk.Radiobutton(mode_row, text="灰度 (gray)", variable=self.mode_var, value="gray", command=self.on_mode_change).pack(side="left", padx=6)
            ttk.Radiobutton(mode_row, text="纯黑白 (bw)", variable=self.mode_var, value="bw", command=self.on_mode_change).pack(side="left", padx=6)
            ttk.Radiobutton(mode_row, text="保留颜色（仅压缩） (color)", variable=self.mode_var, value="color", command=self.on_mode_change).pack(side="left", padx=6)

            thr_row = ttk.Frame(frm_opt); thr_row.pack(fill="x", pady=6, padx=10)
            ttk.Label(thr_row, text="阈值：").pack(side="left")
            self.thr_var = tk.IntVar(value=180)
            self.thr_scale = ttk.Scale(thr_row, from_=0, to=255, orient="horizontal", command=self.on_thr_slide)
            self.thr_scale.pack(side="left", fill="x", expand=True, padx=6)
            self.thr_label = ttk.Label(thr_row, text=str(self.thr_var.get()))
            self.thr_label.pack(side="left")

            dpi_row = ttk.Frame(frm_opt); dpi_row.pack(fill="x", pady=6, padx=10)
            ttk.Label(dpi_row, text="DPI：").pack(side="left")
            self.dpi_var = tk.IntVar(value=200)
            self.dpi_entry = ttk.Entry(dpi_row, textvariable=self.dpi_var, width=8)
            self.dpi_entry.pack(side="left", padx=6)
            ttk.Label(dpi_row, text="（文字略糊可调到 300 或 400）").pack(side="left")

            ds_row = ttk.Frame(frm_opt); ds_row.pack(fill="x", pady=6, padx=10)
            ttk.Label(ds_row, text="下采样（%）：").pack(side="left")
            self.downscale_var = tk.IntVar(value=100)
            self.ds_scale = ttk.Scale(ds_row, from_=50, to=100, orient="horizontal", command=self.on_ds_slide)
            self.ds_scale.pack(side="left", fill="x", expand=True, padx=6)
            self.ds_label = ttk.Label(ds_row, text=str(self.downscale_var.get()))
            self.ds_label.pack(side="left")
            ttk.Label(ds_row, text="（先渲染后再缩小）").pack(side="left", padx=6)

            frm_cmp = ttk.LabelFrame(self.scroll_frame, text="压缩选项（灰度/彩色 可用）")
            frm_cmp.grid(row=2, column=0, sticky="nsew", **pad)

            cmp_row1 = ttk.Frame(frm_cmp); cmp_row1.pack(fill="x", pady=6, padx=10)
            self.use_jpeg_var = tk.BooleanVar(value=True)
            self.jpeg_chk = ttk.Checkbutton(cmp_row1, text="使用 JPEG 压缩（更小，可能轻微失真）", variable=self.use_jpeg_var, command=self.on_mode_change)
            self.jpeg_chk.pack(side="left")

            cmp_row2 = ttk.Frame(frm_cmp); cmp_row2.pack(fill="x", pady=6, padx=10)
            ttk.Label(cmp_row2, text="JPEG 质量：").pack(side="left")
            self.jpeg_q_var = tk.IntVar(value=75)
            self.jpeg_q_scale = ttk.Scale(cmp_row2, from_=30, to=95, orient="horizontal", command=self.on_jpeg_slide)
            self.jpeg_q_scale.pack(side="left", fill="x", expand=True, padx=6)
            self.jpeg_q_label = ttk.Label(cmp_row2, text=str(self.jpeg_q_var.get()))
            self.jpeg_q_label.pack(side="left")
            ttk.Label(cmp_row2, text="（数值越低文件越小）").pack(side="left", padx=6)

            frm_io = ttk.LabelFrame(self.scroll_frame, text="输出设置")
            frm_io.grid(row=3, column=0, sticky="nsew", **pad)

            sfx_row = ttk.Frame(frm_io); sfx_row.pack(fill="x", pady=6, padx=10)
            ttk.Label(sfx_row, text="文件名后缀：").pack(side="left")
            self.suffix_var = tk.StringVar(value="_压缩")
            ttk.Entry(sfx_row, textvariable=self.suffix_var, width=12).pack(side="left", padx=6)

            out_row = ttk.Frame(frm_io); out_row.pack(fill="x", pady=6, padx=10)
            ttk.Label(out_row, text="输出目录：").pack(side="left")
            self.out_dir_var = tk.StringVar(value="")
            self.out_dir_entry = ttk.Entry(out_row, textvariable=self.out_dir_var)
            self.out_dir_entry.pack(side="left", fill="x", expand=True, padx=6)
            ttk.Button(out_row, text="选择…", command=self.choose_out_dir).pack(side="left")

            frm_prog = ttk.LabelFrame(self.scroll_frame, text="进度与日志")
            frm_prog.grid(row=4, column=0, sticky="nsew", **pad)
            frm_prog.columnconfigure(0, weight=1)
            self.progress = ttk.Progressbar(frm_prog, mode="determinate")
            self.progress.grid(row=0, column=0, sticky="ew", padx=10, pady=(12, 6))
            self.stat_label = ttk.Label(frm_prog, text="待机")
            self.stat_label.grid(row=1, column=0, sticky="w", padx=10, pady=(0, 6))

            self.log = tk.Text(frm_prog, height=10, wrap="word")
            self.log.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))
            self.log.configure(state="disabled")

            self.scroll_frame.columnconfigure(0, weight=1)

            try:
                self.thr_scale.set(self.thr_var.get())
                self.ds_scale.set(self.downscale_var.get())
                self.jpeg_q_scale.set(self.jpeg_q_var.get())
            except Exception:
                pass

            self.on_mode_change()

        def add_files(self):
            files = filedialog.askopenfilenames(title="选择 PDF 文件", filetypes=[("PDF files", "*.pdf")])
            for f in files:
                if f and f.lower().endswith(".pdf"):
                    self.listbox.insert("end", f)

        def add_folder(self):
            folder = filedialog.askdirectory(title="选择文件夹")
            if folder:
                self.listbox.insert("end", f"[DIR]{folder}")

        def remove_selected(self):
            for i in reversed(self.listbox.curselection()):
                self.listbox.delete(i)

        def clear_all(self):
            self.listbox.delete(0, "end")

        def choose_out_dir(self):
            d = filedialog.askdirectory(title="选择输出目录")
            if d:
                self.out_dir_var.set(d)

        def on_mode_change(self):
            mode = self.mode_var.get()
            is_bw = mode == "bw"
            # 阈值仅对 bw 有效
            try:
                self._set_widget_state(self.thr_scale, is_bw)
                self._set_widget_state(self.thr_label, is_bw)
            except Exception:
                pass
            # JPEG/质量对 gray 和 color 有效
            self._set_widget_state(self.jpeg_chk, True)
            self._set_widget_state(self.jpeg_q_scale, self.use_jpeg_var.get())
            self._set_widget_state(self.jpeg_q_label, self.use_jpeg_var.get())

        def _set_widget_state(self, widget, enabled: bool):
            try:
                widget.configure(state=("normal" if enabled else "disabled"))
            except Exception:
                try:
                    if enabled:
                        widget.pack()
                    else:
                        widget.pack_forget()
                except Exception:
                    pass

        def on_thr_slide(self, val):
            try:
                ival = int(float(val))
            except Exception:
                ival = getattr(self, "thr_var").get()
            self.thr_var.set(ival)
            if hasattr(self, "thr_label") and self.thr_label is not None:
                try:
                    self.thr_label.config(text=str(ival))
                except Exception:
                    pass

        def on_ds_slide(self, val):
            try:
                ival = int(float(val))
            except Exception:
                ival = getattr(self, "downscale_var").get()
            self.downscale_var.set(ival)
            if hasattr(self, "ds_label") and self.ds_label is not None:
                try:
                    self.ds_label.config(text=str(ival))
                except Exception:
                    pass

        def on_jpeg_slide(self, val):
            try:
                ival = int(float(val))
            except Exception:
                ival = getattr(self, "jpeg_q_var").get()
            self.jpeg_q_var.set(ival)
            if hasattr(self, "jpeg_q_label") and self.jpeg_q_label is not None:
                try:
                    self.jpeg_q_label.config(text=str(ival))
                except Exception:
                    pass

        def log_line(self, text):
            self.log.configure(state="normal")
            self.log.insert("end", text + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")

        def set_status(self, text):
            self.stat_label.config(text=text)

        def poll_queue(self):
            while True:
                try:
                    item = self.queue.get_nowait()
                except queue.Empty:
                    break
                kind, payload = item
                if kind == "status":
                    self.set_status(payload)
                elif kind == "log":
                    self.log_line(payload)
                elif kind == "progress":
                    self.progress["value"] = payload
                elif kind == "done":
                    self.on_done(payload)
            self.after(100, self.poll_queue)

        def start(self):
            items = self.listbox.get(0, "end")
            inputs = []
            for it in items:
                if it.startswith("[DIR]"):
                    inputs.append(("dir", it[5:]))
                else:
                    inputs.append(("file", it))

            if not inputs:
                messagebox.showwarning("提示", "请先添加 PDF 文件或文件夹。")
                return

            try:
                dpi = int(self.dpi_var.get())
                if dpi < 72 or dpi > 1200:
                    raise ValueError
            except Exception:
                messagebox.showerror("错误", "DPI 请输入 72–1200 之间的整数。")
                return

            self.stop_flag.clear()
            self.start_btn.config(state="disabled")
            self.stop_btn.config(state="normal")
            self.progress["value"] = 0
            self.queue.put(("status", "开始转换…"))
            self.log_line("开始转换…")

            mode = self.mode_var.get()
            threshold = int(self.thr_var.get())
            suffix = self.suffix_var.get().strip() or "_cmp"
            out_dir = Path(self.out_dir_var.get().strip()) if self.out_dir_var.get().strip() else None
            use_jpeg = bool(self.use_jpeg_var.get())
            jpeg_quality = int(self.jpeg_q_var.get())
            downscale_percent = int(self.downscale_var.get())

            self.worker = threading.Thread(
                target=self.worker_run,
                args=(inputs, mode, threshold, dpi, suffix, out_dir, use_jpeg, jpeg_quality, downscale_percent),
                daemon=True
            )
            self.worker.start()

        def stop(self):
            self.stop_flag.set()
            self.queue.put(("status", "准备停止…"))

        def on_done(self, summary):
            self.start_btn.config(state="normal")
            self.stop_btn.config(state="disabled")
            self.queue.put(("status", "完成" if not self.stop_flag.is_set() else "已停止"))
            self.log_line(summary)

        def worker_run(self, inputs, mode, threshold, dpi, suffix, out_dir: Path|None,
                       use_jpeg, jpeg_quality, downscale_percent):
            pdfs = []
            for kind, path in inputs:
                p = Path(path)
                if kind == "file" and p.is_file() and p.suffix.lower() == ".pdf":
                    pdfs.append(p)
                elif kind == "dir" and p.is_dir():
                    for f in p.rglob("*.pdf"):
                        pdfs.append(f)

            total = len(pdfs)
            if total == 0:
                self.queue.put(("log", "未找到任何 PDF。"))
                self.queue.put(("done", "未找到任何 PDF。"))
                return

            ok = 0
            for idx, pdf in enumerate(pdfs, start=1):
                if self.stop_flag.is_set():
                    self.queue.put(("log", "用户停止。"))
                    break
                try:
                    if out_dir:
                        out_dir.mkdir(parents=True, exist_ok=True)
                        out_path = out_dir / (pdf.stem + suffix + ".pdf")
                    else:
                        out_path = pdf.with_name(pdf.stem + suffix + pdf.suffix)

                    def cb(s):
                        self.queue.put(("status", s))

                    convert_pdf(
                        pdf, out_path,
                        mode=mode,
                        threshold=threshold,
                        dpi=dpi,
                        use_jpeg=use_jpeg,
                        jpeg_quality=jpeg_quality,
                        downscale_percent=downscale_percent,
                        progress_cb=cb
                    )
                    ok += 1
                    self.queue.put(("log", f"已保存：{out_path}"))
                except Exception as e:
                    self.queue.put(("log", f"[错误] {pdf}: {e}"))
                self.queue.put(("progress", int(idx / total * 100)))

            self.queue.put(("done", f"完成：{ok}/{total} 个文件成功。"))

    app = App()
    app.mainloop()


# -------------------------
# 主入口：解析命令行
# -------------------------
def main():
    parser = argparse.ArgumentParser(description="PDF 压缩工具")
    parser.add_argument("--no-gui", action="store_true", help="不启动 GUI（使用命令行模式）")
    parser.add_argument("--input", "-i", nargs="+", help="要处理的文件或文件夹（命令行模式）")
    parser.add_argument("--out", "-o", help="输出目录（命令行模式）")
    parser.add_argument("--suffix", "-s", default="_压缩", help="输出文件名后缀（默认 _压缩）")
    parser.add_argument("--mode", choices=["gray", "bw", "color"], default="gray", help="处理模式：gray/bw/color（默认 gray）")
    parser.add_argument("--threshold", type=int, default=180, help="二值化阈值，仅对 bw 有效（0-255）")
    parser.add_argument("--dpi", type=int, default=200, help="渲染 DPI（默认 200）")
    parser.add_argument("--jpeg", dest="use_jpeg", action="store_true", help="使用 JPEG 压缩（默认）")
    parser.add_argument("--png", dest="use_jpeg", action="store_false", help="使用 PNG（无损）")
    parser.add_argument("--quality", type=int, default=75, help="JPEG 质量（30-95，默认 75）")
    parser.add_argument("--downscale", type=int, default=100, help="下采样百分比（50-100，默认 100）")
    args = parser.parse_args()

    if args.no_gui or (args.input and len(args.input) > 0):
        # 命令行模式
        if not args.input:
            parser.error("命令行模式请通过 --input 提供文件或文件夹")
        inputs = [Path(p) for p in args.input]
        out_dir = Path(args.out) if args.out else None
        batch_process(inputs, out_dir, suffix=args.suffix, mode=args.mode, threshold=args.threshold,
                      dpi=args.dpi, use_jpeg=bool(args.use_jpeg), jpeg_quality=args.quality,
                      downscale_percent=args.downscale)
    else:
        # 启动 GUI
        run_gui()


if __name__ == "__main__":
    main()
