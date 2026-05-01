"""
Flask GUI 服务器 — 独立的 Web 管理界面

提供工作流的在线浏览、编辑、创建、删除功能。
作为独立线程运行，与 AstrBot 主进程解耦。
"""
import json
import logging
import os
import shutil
import threading
from functools import wraps
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, flash, redirect, render_template, request, session, url_for

logger = logging.getLogger("GuiServer")


class ConfigManager:
    """工作流配置管理器 — 文件系统 CRUD 操作"""

    def __init__(self, config_dir: Path, workflow_dir: Path, main_config_file: Path):
        self.config_dir = config_dir
        self.workflow_dir = workflow_dir
        self.main_config_file = main_config_file

    def get_workflows(self) -> List[Dict[str, Any]]:
        """获取所有工作流列表"""
        workflows = []
        if not self.workflow_dir.exists():
            return workflows
        for wfn in [f.name for f in self.workflow_dir.iterdir()]:
            wf_path = self.workflow_dir / wfn
            if not wf_path.is_dir():
                continue
            config_file = wf_path / "config.json"
            workflow_file = wf_path / "workflow.json"
            if not config_file.exists() or not workflow_file.exists():
                continue
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                with open(workflow_file, 'r', encoding='utf-8') as f:
                    wf_data = json.load(f)
                workflows.append({
                    "name": wfn,
                    "config": config,
                    "workflow": wf_data,
                    "workflow_json_pretty": json.dumps(wf_data, ensure_ascii=False, indent=2),
                    "path": str(wf_path)
                })
            except Exception as e:
                logger.error(f"加载工作流 {wfn} 失败: {e}")
        return workflows

    def save_workflow(self, workflow_name: str, config: Dict[str, Any],
                      workflow_data: Dict[str, Any]) -> bool:
        """保存工作流"""
        try:
            wf_path = self.workflow_dir / workflow_name
            wf_path.mkdir(exist_ok=True)
            with open(wf_path / "config.json", 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            with open(wf_path / "workflow.json", 'w', encoding='utf-8') as f:
                json.dump(workflow_data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"保存工作流 {workflow_name} 失败: {e}")
            return False

    def delete_workflow(self, workflow_name: str) -> bool:
        """删除工作流"""
        try:
            wf_path = self.workflow_dir / workflow_name
            if wf_path.exists() and wf_path.is_dir():
                shutil.rmtree(wf_path)
                return True
            return False
        except Exception as e:
            logger.error(f"删除工作流 {workflow_name} 失败: {e}")
            return False


class GuiServer:
    """Flask GUI 服务器管理类"""

    def __init__(self, config_dir: Path, workflow_dir: Path, main_config_file: Path,
                 gui_port: int = 7777, gui_username: str = "admin", gui_password: str = "admin"):
        self.config_dir = config_dir
        self.workflow_dir = workflow_dir
        self.main_config_file = main_config_file
        self.gui_port = gui_port
        self.gui_username = gui_username
        self.gui_password = gui_password

        self.app: Optional[Flask] = None
        self.gui_thread: Optional[threading.Thread] = None
        self.gui_running = False
        self._config_manager: Optional[ConfigManager] = None

    @property
    def config_manager(self) -> ConfigManager:
        if self._config_manager is None:
            self._config_manager = ConfigManager(
                self.config_dir, self.workflow_dir, self.main_config_file
            )
        return self._config_manager

    def init_app(self) -> None:
        """初始化 Flask 应用"""
        try:
            self.app = Flask(__name__)
            self.app.secret_key = os.environ.get('FLASK_SECRET_KEY') or os.urandom(24)
            logger.info("Flask GUI 应用初始化")

            template_dir = self.config_dir / "templates"
            if template_dir.exists():
                self.app.template_folder = str(template_dir)

            self._register_routes()
            logger.info(f"Flask GUI 应用初始化成功，端口: {self.gui_port}")
        except Exception as e:
            logger.error(f"初始化GUI失败: {e}")
            self.app = None

    def _register_routes(self) -> None:
        """注册所有 Flask 路由"""
        if not self.app:
            return

        def login_required(f):
            @wraps(f)
            def decorated(*args, **kwargs):
                if 'logged_in' not in session:
                    return redirect(url_for('login'))
                return f(*args, **kwargs)
            return decorated

        @self.app.route('/login', methods=['GET', 'POST'])
        def login():
            if request.method == 'POST':
                username = request.form.get('username', '')
                password = request.form.get('password', '')
                if username == self.gui_username and password == self.gui_password:
                    session['logged_in'] = True
                    flash('登录成功！', 'success')
                    return redirect(url_for('index'))
                else:
                    flash('用户名或密码错误！', 'error')
            return render_template('login.html')

        @self.app.route('/logout')
        def logout():
            session.pop('logged_in', None)
            flash('已退出登录！', 'info')
            return redirect(url_for('login'))

        @self.app.route('/')
        @login_required
        def index():
            workflows = self.config_manager.get_workflows()
            return render_template('index.html', workflows=workflows)

        @self.app.route('/workflow/<workflow_name>')
        @login_required
        def workflow_detail(workflow_name):
            workflows = self.config_manager.get_workflows()
            wf = next((w for w in workflows if w['name'] == workflow_name), None)
            if not wf:
                flash('工作流不存在！', 'error')
                return redirect(url_for('index'))
            return render_template('workflow_detail.html', workflow=wf)

        @self.app.route('/workflow/<workflow_name>/edit')
        @login_required
        def workflow_edit(workflow_name):
            workflows = self.config_manager.get_workflows()
            wf = next((w for w in workflows if w['name'] == workflow_name), None)
            if not wf:
                flash('工作流不存在！', 'error')
                return redirect(url_for('index'))
            return render_template('workflow_edit.html', workflow=wf)

        @self.app.route('/workflow/<workflow_name>/save', methods=['POST'])
        @login_required
        def workflow_save(workflow_name):
            try:
                config = json.loads(request.form.get('config', '{}'))
                wf_data = json.loads(request.form.get('workflow', '{}'))
                if self.config_manager.save_workflow(workflow_name, config, wf_data):
                    flash('工作流保存成功！', 'success')
                else:
                    flash('工作流保存失败！', 'error')
                return redirect(url_for('workflow_detail', workflow_name=workflow_name))
            except Exception as e:
                logger.error(f"保存工作流失败: {e}")
                flash(f'保存失败: {str(e)}', 'error')
                return redirect(url_for('workflow_edit', workflow_name=workflow_name))

        @self.app.route('/workflow/new')
        @login_required
        def workflow_new():
            return render_template('workflow_new.html')

        @self.app.route('/workflow/create', methods=['POST'])
        @login_required
        def workflow_create():
            try:
                wfn = request.form.get('workflow_name', '').strip()
                if not wfn:
                    flash('工作流名称不能为空！', 'error')
                    return redirect(url_for('workflow_new'))
                wf_path = self.workflow_dir / wfn
                if wf_path.exists():
                    flash('工作流已存在！', 'error')
                    return redirect(url_for('workflow_new'))

                input_nodes = request.form.getlist('input_nodes')
                output_nodes = request.form.getlist('output_nodes')

                input_mappings = {}
                output_mappings = {}
                for nid in input_nodes:
                    input_mappings[nid] = {
                        "parameter_name": "image", "required": True,
                        "type": "image", "description": "输入图片"
                    }
                for nid in output_nodes:
                    output_mappings[nid] = {
                        "parameter_name": "images", "type": "image",
                        "description": "处理后的图片"
                    }

                config = {
                    "name": request.form.get('name', wfn),
                    "prefix": request.form.get('prefix', ''),
                    "description": request.form.get('description', ''),
                    "version": request.form.get('version', '1.0.0'),
                    "author": request.form.get('author', 'ComfyUI Plugin'),
                    "input_nodes": input_nodes, "output_nodes": output_nodes,
                    "input_mappings": input_mappings, "output_mappings": output_mappings,
                    "configurable_nodes": request.form.getlist('configurable_nodes'),
                    "node_configs": {}
                }

                im_str = request.form.get('input_mappings', '{}')
                if im_str: config['input_mappings'] = json.loads(im_str)
                om_str = request.form.get('output_mappings', '{}')
                if om_str: config['output_mappings'] = json.loads(om_str)
                nc_str = request.form.get('node_configs', '{}')
                if nc_str: config['node_configs'] = json.loads(nc_str)

                wf_data = {}
                if self.config_manager.save_workflow(wfn, config, wf_data):
                    flash('工作流创建成功！', 'success')
                    return redirect(url_for('workflow_detail', workflow_name=wfn))
                else:
                    flash('工作流创建失败！', 'error')
                    return redirect(url_for('workflow_new'))
            except Exception as e:
                logger.error(f"创建工作流失败: {e}")
                flash(f'创建失败: {str(e)}', 'error')
                return redirect(url_for('workflow_new'))

        @self.app.route('/workflow/<workflow_name>/delete', methods=['POST'])
        @login_required
        def workflow_delete(workflow_name):
            try:
                if self.config_manager.delete_workflow(workflow_name):
                    flash('工作流删除成功！', 'success')
                else:
                    flash('工作流删除失败！', 'error')
            except Exception as e:
                logger.error(f"删除工作流失败: {e}")
                flash(f'删除失败: {str(e)}', 'error')
            return redirect(url_for('index'))

    def start(self) -> bool:
        """在新线程中启动 GUI 服务器"""
        if self.gui_running or not self.app:
            return False

        def run():
            try:
                logger.info(f"启动ComfyUI配置管理界面...")
                logger.info(f"配置目录: {self.config_dir}")
                logger.info(f"工作流目录: {self.workflow_dir}")
                logger.info(f"访问地址: http://0.0.0.0:{self.gui_port}")
                logger.info(f"管理员账号: {self.gui_username}")
                logger.info("=" * 50)

                try:
                    import importlib.util
                    if importlib.util.find_spec("gunicorn") is not None:
                        from gunicorn.app.base import BaseApplication

                        class GunicornApp(BaseApplication):
                            def __init__(self, app, options=None):
                                self.options = options or {}
                                self.application = app
                                super().__init__()
                            def load_config(self):
                                cfg = {k: v for k, v in self.options.items()
                                       if k in self.cfg.settings and v is not None}
                                for k, v in cfg.items():
                                    self.cfg.set(k.lower(), v)
                            def load(self):
                                return self.application

                        GunicornApp(self.app, {
                            'bind': f'0.0.0.0:{self.gui_port}',
                            'workers': 2, 'threads': 2, 'worker_class': 'gthread',
                            'timeout': 120, 'keepalive': 2, 'max_requests': 1000,
                            'max_requests_jitter': 100, 'preload_app': True,
                            'accesslog': '-', 'errorlog': '-', 'loglevel': 'info'
                        }).run()
                    elif importlib.util.find_spec("waitress") is not None:
                        from waitress import serve
                        logger.info("使用 Waitress WSGI 服务器")
                        serve(self.app, host='0.0.0.0', port=self.gui_port, threads=4)
                    else:
                        logger.info("使用 Flask 开发服务器")
                        self.app.run(host='0.0.0.0', port=self.gui_port, debug=False, threaded=True)
                except Exception:
                    logger.info("使用 Flask 开发服务器")
                    self.app.run(host='0.0.0.0', port=self.gui_port, debug=False, threaded=True)
            except Exception as e:
                logger.error(f"GUI服务器启动失败: {e}")

        self.gui_thread = threading.Thread(target=run, daemon=True)
        self.gui_thread.start()
        self.gui_running = True
        logger.info("GUI服务器已启动")
        return True

    def stop(self) -> None:
        """停止 GUI 服务器"""
        if self.gui_running:
            logger.info("正在停止GUI服务器...")
            self.gui_running = False
            logger.info("GUI服务器已停止")
