# AIOps 运维监控平台

面向机房管理员的智能运维监控平台，支持服务器、VMware虚拟化、网络设备、存储设备、温湿度环境监控，集成内网千问大模型实现告警智能分析。

## 功能特性

- **服务器监控**：Linux（CentOS/openEuler/Rocky/Ubuntu/龙蜥）+ Windows（2008-2019），CPU/内存/磁盘/网络/端口/服务
- **VMware监控**：对接vCenter/ESXi，虚拟机状态、资源使用
- **网络设备**：华三/华为/Dell交换机SNMP监控（CPU/内存/端口状态）
- **存储设备**：华为/华三存储容量监控
- **温湿度监控**：机房环境传感器接入
- **告警引擎**：阈值告警 + 告警确认/解决流程
- **AI助手**：内网千问大模型，告警智能分析、运维问答

## 快速开始

### 1. 启动后端

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
```

### 2. 启动前端

```bash
cd frontend
npm install
npm run dev
```

### 3. 部署Agent

将 `agent/` 目录复制到被监控服务器，修改 `config.yaml` 中的服务端地址，运行 `python agent.py`。

### 4. 访问平台

浏览器打开 `http://localhost:5173`，默认账号 **admin / admin123**

## 详细文档

见 [docs/部署文档.md](docs/部署文档.md)
