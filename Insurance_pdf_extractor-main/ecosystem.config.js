module.exports = {
    apps: [
        {
            name: "data-extraction-app",
            script: "backend/production_server.py",
            interpreter: "c:/Users/Intern/data extraction/venv/Scripts/python.exe",
            env: {
                PORT: 5000,
                FLASK_ENV: "production",
                PYTHONUTF8: "1"
            }
        }
    ]
};
