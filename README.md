# ⚡ ANSHHOSTING — Cloud Hosting Platform

A full-featured web hosting panel similar to Railway/Heroku.  
Deploy Python and Node.js apps directly from your browser.

---

## 🚀 Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Run the Platform
```bash
python app.py
```

### 3. Open Browser
```
http://localhost:5000
```

### 4. Register First Account
- The **first registered user** automatically becomes **Admin**
- All other users are regular developers

---

## 📁 Project Structure
```
anshhosting/
├── app.py              ← Main Flask application
├── users.json          ← User database
├── projects.json       ← Project database
├── requirements.txt    ← Python dependencies
├── uploads/            ← Uploaded zip files
├── projects/           ← Deployed project folders
├── templates/
│   ├── login.html
│   ├── register.html
│   ├── dashboard.html
│   ├── project.html
│   ├── terminal.html
│   ├── upload.html
│   └── admin.html
└── static/
    ├── style.css
    └── script.js
```

---

## ✨ Features

| Feature | Details |
|---------|---------|
| **Auth** | Register, Login, Logout, Sessions, Password Hashing |
| **Deploy** | Upload .py, .js, .zip — auto-detects language |
| **Python** | Auto venv, pip install from requirements.txt |
| **Node.js** | Auto npm install from package.json |
| **Ports** | Each project gets unique port (5001–5100) |
| **Logs** | Live terminal with AJAX polling (1s refresh) |
| **Controls** | Start / Stop / Restart / Delete per project |
| **Files** | View, upload, delete project files |
| **Admin** | All users, all projects, server resource stats |
| **Security** | Path traversal prevention, file type limits, 200MB cap |

---

## 🐍 Python App Example

Create `main.py`:
```python
import os
from flask import Flask
app = Flask(__name__)

@app.route('/')
def home():
    return "Hello from ANSHHOSTING!"

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port)
```

Optional `requirements.txt`:
```
flask
```

---

## 💚 Node.js App Example

Create `index.js`:
```javascript
const http = require('http');
const port = process.env.PORT || 3000;

const server = http.createServer((req, res) => {
  res.writeHead(200, { 'Content-Type': 'text/plain' });
  res.end('Hello from ANSHHOSTING!\n');
});

server.listen(port, () => {
  console.log(`Server running on port ${port}`);
});
```

---

## 🌐 Deploy on VPS (Ubuntu)

```bash
# Install Python & Node.js
sudo apt update
sudo apt install python3 python3-pip python3-venv nodejs npm -y

# Clone / copy project
cd /opt
# Copy anshhosting folder here

# Install deps
pip3 install -r requirements.txt

# Run with Gunicorn (production)
pip3 install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app

# Or with systemd service for auto-restart
```

## 🌐 Deploy on Render / Railway

Set build command: `pip install -r requirements.txt`  
Set start command: `python app.py`  
Set PORT environment variable if needed.

---

## 🔐 Security Notes

- Passwords hashed with SHA-256
- File uploads restricted to safe extensions
- Path traversal prevention on file operations
- 200MB max upload size
- Session-based authentication

---

## 📝 API Endpoints

| Method | URL | Description |
|--------|-----|-------------|
| GET/POST | `/login` | Login page |
| GET/POST | `/register` | Register page |
| GET | `/dashboard` | Main dashboard |
| GET/POST | `/upload` | Deploy new project |
| GET | `/project/<id>` | Project details |
| POST | `/project/<id>/start` | Start project |
| POST | `/project/<id>/stop` | Stop project |
| POST | `/project/<id>/restart` | Restart project |
| POST | `/project/<id>/delete` | Delete project |
| GET | `/project/<id>/logs` | Terminal page |
| GET | `/api/logs/<id>` | JSON log stream |
| GET | `/project/<id>/status` | JSON status |
| GET | `/admin` | Admin panel |
