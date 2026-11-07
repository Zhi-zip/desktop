# Oringal Script By zhi-xiaoer

import sys
import os
import time
import zipfile
import json
import tarfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Dict, List, Optional, Type

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QPushButton, QVBoxLayout,
    QProgressBar, QFileDialog, QWidget, QLabel, QHBoxLayout,
    QStackedWidget, QLineEdit, QListWidget, QListWidgetItem,
    QButtonGroup, QMessageBox, QAbstractItemView, QComboBox
)
from PySide6.QtCore import QThread, Signal, Qt, QTimer, QStandardPaths

try:
    import py7zr
except ImportError:
    py7zr = None

try:
    import rarfile
except ImportError:
    rarfile = None


class ArchiveError(Exception):
    pass


class ArchiveHandler(ABC):
    supports_append: bool = False
    supports_compress: bool = True

    @abstractmethod
    def compress(self, sources: List[Path], output_path: Path,
                 progress_cb: Optional[Callable[[int, int, str], None]] = None,
                 password: Optional[str] = None, **options) -> None:
        ...

    @abstractmethod
    def extract(self, archive_path: Path, output_dir: Path,
                progress_cb: Optional[Callable[[int, int, str], None]] = None,
                password: Optional[str] = None, **options) -> None:
        ...

    @abstractmethod
    def list_contents(self, archive_path: Path,
                      password: Optional[str] = None) -> List[str]:
        ...


class ZipHandler(ArchiveHandler):
    supports_append = True
    supports_compress = True

    def compress(self, sources, output_path, progress_cb=None, password=None, **options):
        compression = options.get("compression", zipfile.ZIP_DEFLATED)
        total = len(sources)
        try:
            with zipfile.ZipFile(output_path, "w", compression=compression) as zf:
                for i, src in enumerate(sources):
                    if src.is_dir():
                        for file in src.rglob("*"):
                            if file.is_file():
                                zf.write(file, file.relative_to(src.parent))
                    else:
                        zf.write(src, src.name)
                    if progress_cb:
                        progress_cb(i + 1, total, f"已加入 {src.name}")
        except Exception as e:
            raise ArchiveError(f"ZIP 压缩失败: {e}") from e

    def extract(self, archive_path, output_dir, progress_cb=None, password=None, **options):
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                pwd = password.encode() if password else None
                members = zf.namelist()
                total = len(members)
                for i, member in enumerate(members):
                    zf.extract(member, output_dir, pwd=pwd)
                    if progress_cb:
                        progress_cb(i + 1, total, f"解压 {member}")
        except Exception as e:
            raise ArchiveError(f"ZIP 解压失败: {e}") from e

    def list_contents(self, archive_path, password=None):
        with zipfile.ZipFile(archive_path, "r") as zf:
            return zf.namelist()


_TAR_WRITE_MODES = {
    ".tar": "w", ".tar.gz": "w:gz", ".tgz": "w:gz",
    ".tar.bz2": "w:bz2", ".tbz2": "w:bz2",
    ".tar.xz": "w:xz", ".txz": "w:xz",
}


class TarHandler(ArchiveHandler):
    supports_append = False
    supports_compress = True

    def _write_mode(self, output_path: Path) -> str:
        for suffix, mode in _TAR_WRITE_MODES.items():
            if str(output_path).endswith(suffix):
                return mode
        raise ArchiveError(f"不支持的 TAR 输出后缀: {output_path.suffix}")

    def compress(self, sources, output_path, progress_cb=None, password=None, **options):
        mode = self._write_mode(output_path)
        total = len(sources)
        try:
            with tarfile.open(output_path, mode) as tf:
                for i, src in enumerate(sources):
                    tf.add(src, arcname=src.name)
                    if progress_cb:
                        progress_cb(i + 1, total, f"已加入 {src.name}")
        except Exception as e:
            raise ArchiveError(f"TAR 压缩失败: {e}") from e

    def extract(self, archive_path, output_dir, progress_cb=None, password=None, **options):
        try:
            with tarfile.open(archive_path, "r:*") as tf:
                members = tf.getmembers()
                total = len(members)
                for i, member in enumerate(members):
                    tf.extract(member, output_dir)
                    if progress_cb:
                        progress_cb(i + 1, total, f"解压 {member.name}")
        except Exception as e:
            raise ArchiveError(f"TAR 解压失败: {e}") from e

    def list_contents(self, archive_path, password=None):
        with tarfile.open(archive_path, "r:*") as tf:
            return tf.getnames()


class SevenZipHandler(ArchiveHandler):
    supports_append = False
    supports_compress = True

    def _require(self):
        if py7zr is None:
            raise ArchiveError("7Z 支持需要安装 py7zr: pip install py7zr")

    def compress(self, sources, output_path, progress_cb=None, password=None, **options):
        self._require()
        total = len(sources)
        try:
            with py7zr.SevenZipFile(output_path, "w", password=password) as archive:
                for i, src in enumerate(sources):
                    arcname = src.name if src.is_file() else ""
                    archive.writeall(src, arcname=arcname)
                    if progress_cb:
                        progress_cb(i + 1, total, f"已加入 {src.name}")
        except Exception as e:
            raise ArchiveError(f"7Z 压缩失败: {e}") from e

    def extract(self, archive_path, output_dir, progress_cb=None, password=None, **options):
        self._require()
        try:
            with py7zr.SevenZipFile(archive_path, "r", password=password) as archive:
                names = archive.getnames()
                total = len(names)
                archive.extractall(path=output_dir)
                if progress_cb:
                    progress_cb(total, total, f"已解压 {total} 个条目")
        except Exception as e:
            raise ArchiveError(f"7Z 解压失败: {e}") from e

    def list_contents(self, archive_path, password=None):
        self._require()
        with py7zr.SevenZipFile(archive_path, "r", password=password) as archive:
            return archive.getnames()


class RarHandler(ArchiveHandler):
    supports_append = False
    supports_compress = False

    _MISSING_TOOL_MSG = (
        "解压 RAR 需要系统安装 unrar 或 bsdtar。\n"
        "Windows: 将 unrar.exe 放入 PATH，或安装 WinRAR 后把安装目录加入 PATH。\n"
        "Linux: 运行 `sudo apt install unrar` 或 `sudo apt install bsdtar`。\n"
        "macOS: 运行 `brew install unar` 或 `brew install libarchive`。"
    )

    def _require(self):
        if rarfile is None:
            raise ArchiveError("RAR 支持需要安装 rarfile: pip install rarfile")

    def compress(self, *args, **kwargs):
        raise ArchiveError("Zhi-zip 不支持创建 RAR 文件，RAR 为专有格式，仅支持解压。")

    def extract(self, archive_path, output_dir, progress_cb=None, password=None, **options):
        self._require()
        pwd = password.encode() if password else None
        try:
            with rarfile.RarFile(archive_path) as rf:
                members = rf.namelist()
                total = len(members)
                for i, member in enumerate(members):
                    rf.extract(member, output_dir, pwd=pwd)
                    if progress_cb:
                        progress_cb(i + 1, total, f"解压 {member}")
        except rarfile.RarCannotExec as e:
            raise ArchiveError(self._MISSING_TOOL_MSG) from e
        except Exception as e:
            raise ArchiveError(f"RAR 解压失败: {e}") from e

    def list_contents(self, archive_path, password=None):
        self._require()
        pwd = password.encode() if password else None
        try:
            with rarfile.RarFile(archive_path) as rf:
                return rf.namelist()
        except rarfile.RarCannotExec as e:
            raise ArchiveError(self._MISSING_TOOL_MSG) from e


FORMAT_REGISTRY: Dict[str, Type[ArchiveHandler]] = {
    ".tar.gz": TarHandler, ".tgz": TarHandler,
    ".tar.bz2": TarHandler, ".tbz2": TarHandler,
    ".tar.xz": TarHandler, ".txz": TarHandler,
    ".tar": TarHandler, ".7z": SevenZipHandler,
    ".rar": RarHandler, ".zip": ZipHandler,
}

FORMAT_LABELS = {
    ".zip": "ZIP", ".tar": "TAR", ".tar.gz": "TAR.GZ",
    ".tgz": "TGZ", ".tar.bz2": "TAR.BZ2", ".tbz2": "TBZ2",
    ".tar.xz": "TAR.XZ", ".txz": "TXZ", ".7z": "7Z",
    ".rar": "RAR（仅解压）",
}

COMPRESSIBLE_SUFFIXES = [s for s, h in FORMAT_REGISTRY.items() if h.supports_compress]


def _match_suffix(path: Path) -> Optional[str]:
    name = path.name.lower()
    for suffix in sorted(FORMAT_REGISTRY.keys(), key=len, reverse=True):
        if name.endswith(suffix):
            return suffix
    return None


def get_handler_for_path(path: Path) -> ArchiveHandler:
    suffix = _match_suffix(path)
    if suffix is None:
        raise ArchiveError(f"不支持的归档格式: {path.name}")
    return FORMAT_REGISTRY[suffix]()


def can_append(suffix: str) -> bool:
    h = FORMAT_REGISTRY.get(suffix.lower())
    return h is not None and h.supports_append


class CompressWorker(QThread):
    progress = Signal(int)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, files, output_path, parent=None):
        super().__init__(parent)
        self.files = list(files)
        self.output_path = output_path
        self._cancel = False
        self._preexisting = os.path.exists(output_path)

    def cancel(self):
        self._cancel = True

    def run(self):
        if not self.files:
            self.failed.emit("没有可压缩的文件。")
            return

        sources = []
        skipped = []
        out_abs = os.path.abspath(self.output_path)

        for f in self.files:
            if self._cancel:
                self._cleanup()
                self.failed.emit("用户取消压缩。")
                return
            if os.path.abspath(f) == out_abs:
                continue
            if not os.path.exists(f):
                skipped.append(f)
                continue
            sources.append(Path(f))

        if not sources:
            self.failed.emit("没有有效的可压缩项。")
            return

        try:
            handler = get_handler_for_path(Path(self.output_path))

            def progress_cb(current, total, message):
                if self._cancel:
                    raise RuntimeError("用户取消压缩。")
                self.progress.emit(int(current / total * 100))

            handler.compress(sources, Path(self.output_path), progress_cb=progress_cb)

        except RuntimeError:
            self._cleanup()
            self.failed.emit("用户取消压缩。")
            return
        except ArchiveError as e:
            self._cleanup()
            self.failed.emit(str(e))
            return
        except Exception as e:
            self._cleanup()
            self.failed.emit(f"压缩失败 [{type(e).__name__}]：{e}")
            return

        msg = f"压缩完成，共 {len(sources)} 个文件。"
        if skipped:
            msg += f" 跳过 {len(skipped)} 个无效项。"
        self.done.emit(msg)

    def _cleanup(self):
        if self._preexisting:
            return
        try:
            if os.path.exists(self.output_path):
                os.remove(self.output_path)
        except OSError:
            pass


class ExtractWorker(QThread):
    progress = Signal(int)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, archive_path, out_dir, parent=None):
        super().__init__(parent)
        self.archive_path = archive_path
        self.out_dir = out_dir
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            handler = get_handler_for_path(Path(self.archive_path))

            def progress_cb(current, total, message):
                if self._cancel:
                    raise RuntimeError("用户取消解压。")
                self.progress.emit(int(current / total * 100))

            handler.extract(Path(self.archive_path), Path(self.out_dir), progress_cb=progress_cb)

        except RuntimeError:
            self.failed.emit("用户取消解压。")
            return
        except ArchiveError as e:
            self.failed.emit(str(e))
            return
        except Exception as e:
            self.failed.emit(f"解压失败 [{type(e).__name__}]：{e}")
            return

        self.done.emit("解压完成。")


class ZhiZipWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Zhi-zip")
        self.resize(760, 520)
        self.files = []
        self.zip_to_extract = ""
        try:
            base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        except Exception:
            base_dir = os.getcwd()
        self.config_file = os.path.join(base_dir, "zhi_config.json")
        self.config = self.load_config()

        main_widget = QWidget()
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        nav_widget = QWidget()
        nav_widget.setFixedWidth(150)
        nav_widget.setStyleSheet("background-color: #f3f3f3;")
        nav_layout = QVBoxLayout(nav_widget)
        nav_layout.setContentsMargins(10, 20, 10, 20)
        nav_layout.setSpacing(8)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.btn_nav_zip = self.create_nav_btn("📦 压缩")
        self.btn_nav_unzip = self.create_nav_btn("📂 解压")
        self.btn_nav_settings = self.create_nav_btn("⚙️ 设置")
        self.btn_nav_about = self.create_nav_btn("ℹ️ 关于")
        for idx, btn in enumerate((self.btn_nav_zip, self.btn_nav_unzip,
                                   self.btn_nav_settings, self.btn_nav_about)):
            self.nav_group.addButton(btn, idx)
            nav_layout.addWidget(btn)
        nav_layout.addStretch()
        self.btn_nav_zip.setChecked(True)

        separator = QWidget()
        separator.setFixedWidth(1)
        separator.setStyleSheet("background-color: #dcdcdc;")

        self.stacked_widget = QStackedWidget()
        self.stacked_widget.setStyleSheet("background-color: #fafafa;")

        main_layout.addWidget(nav_widget)
        main_layout.addWidget(separator)
        main_layout.addWidget(self.stacked_widget, 1)
        self.setCentralWidget(main_widget)

        self.init_zip_page()
        self.init_unzip_page()
        self.init_settings_page()
        self.init_about_page()
        self.nav_group.idClicked.connect(self.stacked_widget.setCurrentIndex)

    def create_nav_btn(self, text):
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setMinimumHeight(46)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet("""
            QPushButton {
                background-color: transparent; border: none;
                border-radius: 8px; font-size: 15px; color: #333333;
                text-align: left; padding-left: 16px;
            }
            QPushButton:hover { background-color: #e5e5e5; }
            QPushButton:checked {
                background-color: #0078d7; color: white; font-weight: bold;
            }
        """)
        return btn

    def init_zip_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(12)

        title = QLabel("📦 压缩")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #222;")

        self.lbl_zip_status = QLabel("请选择文件或文件夹")
        self.lbl_zip_status.setStyleSheet("font-size: 13px; color: #666;")

        btn_layout = QHBoxLayout()
        self.btn_add_file = QPushButton("添加文件")
        self.btn_add_file.clicked.connect(self.select_files)
        self.btn_add_folder = QPushButton("添加文件夹")
        self.btn_add_folder.clicked.connect(self.select_folder)
        self.btn_clear_files = QPushButton("清空列表")
        self.btn_clear_files.clicked.connect(self.clear_files)
        for b in (self.btn_add_file, self.btn_add_folder, self.btn_clear_files):
            b.setFixedHeight(32)
            b.setStyleSheet("""
                QPushButton {
                    background: #ffffff; border: 1px solid #c8c8c8;
                    border-radius: 5px; padding: 0 14px; font-size: 13px;
                }
                QPushButton:hover { background: #eef5fc; border-color: #0078d7; }
            """)
            btn_layout.addWidget(b)
        btn_layout.addStretch()

        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list.setStyleSheet(
            "QListWidget { background: white; border: 1px solid #ddd;"
            " border-radius: 6px; padding: 4px; }"
            "QListWidget::item { padding: 3px 6px; }"
            "QListWidget::item:selected { background: #cfe6fb; color: #000; }"
        )

        self.zip_progress = QProgressBar()
        self.zip_progress.setFixedHeight(22)

        self.btn_start_compress = QPushButton("开始压缩")
        self.btn_start_compress.setFixedHeight(42)
        self.btn_start_compress.setStyleSheet("""
            QPushButton {
                background-color: #0078d7; color: white; border: none;
                border-radius: 6px; font-size: 15px; font-weight: bold;
            }
            QPushButton:hover { background-color: #005a9e; }
            QPushButton:disabled { background-color: #c8c8c8; }
        """)
        self.btn_start_compress.clicked.connect(self.start_zip)

        layout.addWidget(title)
        layout.addWidget(self.lbl_zip_status)
        layout.addLayout(btn_layout)
        layout.addWidget(self.file_list, 1)
        layout.addWidget(self.zip_progress)
        layout.addWidget(self.btn_start_compress)
        self.stacked_widget.addWidget(page)

    def init_unzip_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(12)

        title = QLabel("📂 解压")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #222;")

        row_zip = QHBoxLayout()
        self.input_zip_path = QLineEdit()
        self.input_zip_path.setPlaceholderText("请选择要解压的归档文件…")
        self.input_zip_path.setReadOnly(True)
        self.input_zip_path.setFixedHeight(34)
        self.input_zip_path.setStyleSheet(
            "QLineEdit { border: 1px solid #ccc; border-radius: 5px;"
            " padding: 4px 8px; background: white; }"
        )
        btn_pick_zip = QPushButton("选择文件")
        btn_pick_zip.setFixedHeight(34)
        btn_pick_zip.clicked.connect(self.select_zip)
        btn_pick_zip.setStyleSheet(
            "QPushButton { background: #ffffff; border: 1px solid #c8c8c8;"
            " border-radius: 5px; padding: 0 14px; font-size: 13px; }"
            "QPushButton:hover { background: #eef5fc; border-color: #0078d7; }"
        )
        row_zip.addWidget(self.input_zip_path, 1)
        row_zip.addWidget(btn_pick_zip)

        row_out = QHBoxLayout()
        self.input_out_dir = QLineEdit()
        self.input_out_dir.setPlaceholderText("请选择解压输出目录…")
        self.input_out_dir.setFixedHeight(34)
        self.input_out_dir.setStyleSheet(
            "QLineEdit { border: 1px solid #ccc; border-radius: 5px;"
            " padding: 4px 8px; background: white; }"
        )
        default_out = os.path.join(os.path.expanduser("~"), "Desktop")
        if os.path.isdir(default_out):
            self.input_out_dir.setText(default_out)
        btn_pick_out = QPushButton("浏览…")
        btn_pick_out.setFixedHeight(34)
        btn_pick_out.clicked.connect(self.select_out_dir)
        btn_pick_out.setStyleSheet(
            "QPushButton { background: #ffffff; border: 1px solid #c8c8c8;"
            " border-radius: 5px; padding: 0 14px; font-size: 13px; }"
            "QPushButton:hover { background: #eef5fc; border-color: #0078d7; }"
        )
        row_out.addWidget(self.input_out_dir, 1)
        row_out.addWidget(btn_pick_out)

        self.lbl_unzip_status = QLabel("")
        self.lbl_unzip_status.setStyleSheet("font-size: 13px; color: #666;")

        self.unzip_progress = QProgressBar()
        self.unzip_progress.setFixedHeight(22)

        self.btn_start_extract = QPushButton("开始解压")
        self.btn_start_extract.setFixedHeight(42)
        self.btn_start_extract.setStyleSheet("""
            QPushButton {
                background-color: #28a745; color: white; border: none;
                border-radius: 6px; font-size: 15px; font-weight: bold;
            }
            QPushButton:hover { background-color: #218838; }
            QPushButton:disabled { background-color: #c8c8c8; }
        """)
        self.btn_start_extract.clicked.connect(self.start_unzip)

        layout.addWidget(title)
        layout.addSpacing(6)
        layout.addWidget(QLabel("归档文件："))
        layout.addLayout(row_zip)
        layout.addWidget(QLabel("输出目录："))
        layout.addLayout(row_out)
        layout.addWidget(self.lbl_unzip_status)
        layout.addStretch()
        layout.addWidget(self.unzip_progress)
        layout.addWidget(self.btn_start_extract)
        self.stacked_widget.addWidget(page)

    def init_settings_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(14)

        title = QLabel("⚙️ 设置")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #222;")

        lbl_name = QLabel("默认压缩文件名 (不含后缀):")
        lbl_name.setStyleSheet("font-size: 13px; color: #444;")

        self.input_filename = QLineEdit()
        self.input_filename.setText(self.config.get("default_name", "zhi"))
        self.input_filename.setFixedHeight(34)
        self.input_filename.setStyleSheet(
            "border: 1px solid #ccc; border-radius: 5px; padding: 4px 8px;"
        )

        self.btn_save = QPushButton("保存设置")
        self.btn_save.setFixedHeight(40)
        self.btn_save.setStyleSheet("""
            QPushButton {
                background-color: #28a745; color: white; border: none;
                border-radius: 6px; font-size: 15px; font-weight: bold;
            }
            QPushButton:hover { background-color: #218838; }
        """)
        self.btn_save.clicked.connect(self.save_settings)

        self.lbl_save_status = QLabel("")
        self.lbl_save_status.setStyleSheet("color: #28a745; font-size: 13px;")

        layout.addWidget(title)
        layout.addWidget(lbl_name)
        layout.addWidget(self.input_filename)
        layout.addWidget(self.btn_save)
        layout.addWidget(self.lbl_save_status)
        layout.addStretch()
        self.stacked_widget.addWidget(page)

    def init_about_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_text = """
        <h2>Zhi-zip</h2>
        <p>作者：志校儿</p>
        <p>合作者：玩游戏的鲁邦</p>
        <p>项目地址：<a href='https://github.com/zhi-xiaoer/Zhi-zip'>github.com/zhi-xiaoer/Zhi-zip</a></p>
        """
        lbl_about = QLabel(about_text)
        lbl_about.setOpenExternalLinks(True)
        lbl_about.setStyleSheet("font-size: 14px; line-height: 1.8;")
        layout.addWidget(lbl_about)
        self.stacked_widget.addWidget(page)

    def load_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {"default_name": "zhi"}

    def save_settings(self):
        name = self.input_filename.text().strip()
        if not name:
            name = "zhi"
        self.input_filename.setText(name)
        self.config["default_name"] = name
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
            self.lbl_save_status.setStyleSheet("color: #28a745; font-size: 13px;")
            self.lbl_save_status.setText("✅ 设置已保存！")
        except Exception as e:
            self.lbl_save_status.setStyleSheet("color: #d9534f; font-size: 13px;")
            self.lbl_save_status.setText(f"❌ 保存失败：{e}")
            return
        QTimer.singleShot(2000, lambda: self.lbl_save_status.setText(""))

    def _add_files(self, paths):
        seen = set(self.files)
        added = 0
        for p in paths:
            ap = os.path.abspath(p)
            if ap not in seen:
                seen.add(ap)
                self.files.append(ap)
                item = QListWidgetItem(os.path.basename(ap))
                item.setToolTip(ap)
                item.setData(Qt.ItemDataRole.UserRole, ap)
                self.file_list.addItem(item)
                added += 1
        if added == 0:
            self.lbl_zip_status.setText(f"⚠️ 没有新增文件 (当前共 {len(self.files)} 个)")
        else:
            self.lbl_zip_status.setText(f"✅ 已添加 {len(self.files)} 个文件 (可继续添加)")

    def select_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "选择文件")
        if files:
            self._add_files(files)

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if not folder:
            return
        collected = []
        for root, _, filenames in os.walk(folder):
            for f in filenames:
                collected.append(os.path.join(root, f))
        if collected:
            self._add_files(collected)
        else:
            self.lbl_zip_status.setText("⚠️ 该文件夹为空。")

    def clear_files(self):
        self.files = []
        self.file_list.clear()
        self.lbl_zip_status.setText("请选择文件或文件夹")

    def start_zip(self):
        if not self.files:
            self.lbl_zip_status.setText("❌ 请先添加文件或文件夹！")
            return
        if getattr(self, "zip_worker", None) and self.zip_worker.isRunning():
            return

        default_name = self.config.get("default_name", "zhi")

        format_filter = (
            "所有支持的格式 ("
            + " ".join(f"*{s}" for s in COMPRESSIBLE_SUFFIXES)
            + ");;"
            "ZIP (*.zip);;"
            "TAR (*.tar);;"
            "TAR.GZ (*.tar.gz *.tgz);;"
            "TAR.BZ2 (*.tar.bz2 *.tbz2);;"
            "TAR.XZ (*.tar.xz *.txz);;"
            "7Z (*.7z);;"
            "所有文件 (*.*)"
        )

        docs = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DocumentsLocation)
        default_path = os.path.join(docs, f"{default_name}.zip") if docs else f"{default_name}.zip"

        output, _ = QFileDialog.getSaveFileName(
            self, "保存为", default_path, format_filter)

        if not output:
            return

        if _match_suffix(Path(output)) is None:
            output += ".zip"

        self.btn_start_compress.setEnabled(False)
        self.zip_progress.setValue(0)

        self.zip_worker = CompressWorker(self.files, output, parent=self)
        self.zip_worker.progress.connect(self.zip_progress.setValue)
        self.zip_worker.done.connect(self.on_zip_finished)
        self.zip_worker.failed.connect(self.on_zip_error)
        self.zip_worker.start()

    def on_zip_finished(self, msg):
        self.zip_progress.setValue(100)
        self.btn_start_compress.setEnabled(True)
        self.files = []
        self.file_list.clear()
        self.lbl_zip_status.setText(f"✅ {msg} (列表已清空)")

    def on_zip_error(self, msg):
        self.zip_progress.setValue(0)
        self.btn_start_compress.setEnabled(True)
        self.lbl_zip_status.setText(f"❌ {msg}")

    def select_zip(self):
        filters = (
            "所有支持的归档 ("
            + " ".join(f"*{s}" for s in FORMAT_REGISTRY.keys())
            + ");;"
            "ZIP (*.zip);;"
            "TAR (*.tar *.tar.gz *.tgz *.tar.bz2 *.tbz2 *.tar.xz *.txz);;"
            "7Z (*.7z);;"
            "RAR (*.rar);;"
            "所有文件 (*.*)"
        )
        path, _ = QFileDialog.getOpenFileName(
            self, "选择归档文件", "", filters)
        if path:
            self.zip_to_extract = path
            self.input_zip_path.setText(path)
            if not self.input_out_dir.text().strip():
                self.input_out_dir.setText(os.path.dirname(path))

    def select_out_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if folder:
            self.input_out_dir.setText(folder)

    def start_unzip(self):
        zip_path = self.input_zip_path.text().strip()
        out_dir = self.input_out_dir.text().strip()

        if not zip_path or not os.path.isfile(zip_path):
            self.lbl_unzip_status.setText("❌ 请先选择有效的归档文件！")
            return
        if not out_dir:
            self.lbl_unzip_status.setText("❌ 请选择输出目录！")
            return
        if getattr(self, "unzip_worker", None) and self.unzip_worker.isRunning():
            return

        reply = QMessageBox.question(
            self, "确认解压",
            f"将把内容解压到：\n{out_dir}\n\n同名文件会被覆盖，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.btn_start_extract.setEnabled(False)
        self.unzip_progress.setValue(0)
        self.lbl_unzip_status.setText("正在解压…")

        self.unzip_worker = ExtractWorker(zip_path, out_dir, parent=self)
        self.unzip_worker.progress.connect(self.unzip_progress.setValue)
        self.unzip_worker.done.connect(self.on_unzip_finished)
        self.unzip_worker.failed.connect(self.on_unzip_error)
        self.unzip_worker.start()

    def on_unzip_finished(self, msg):
        self.unzip_progress.setValue(100)
        self.btn_start_extract.setEnabled(True)
        self.lbl_unzip_status.setText(f"✅ {msg}")

    def on_unzip_error(self, msg):
        self.unzip_progress.setValue(0)
        self.btn_start_extract.setEnabled(True)
        self.lbl_unzip_status.setText(f"❌ {msg}")

    def closeEvent(self, event):
        for attr in ("zip_worker", "unzip_worker"):
            w = getattr(self, attr, None)
            if w is not None and w.isRunning():
                w.cancel()
                w.wait(3000)
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ZhiZipWindow()
    window.show()
    sys.exit(app.exec())
