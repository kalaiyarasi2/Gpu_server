# PDF Invoice Extractor: Deployment Guide

This guide explains how to set up the application so that multiple people can use it. There are two ways to do this:

---

## Scenario 1: Network Hosting (Recommended)
**One PC hosts the application, and others access it via their web browser.** This is the easiest for your team because only one person needs to manage the installation.

### 1. Identify the "Server" PC
Choose one PC that will stay on while people are using the app. This will be the "Server".

### 2. Prepare the Server
1. Ensure the project folder is on this PC.
2. Run `setup_env.bat` (if not already done) to install dependencies.
3. Ensure your `.env` file contains the `OPENAI_API_KEY`.
4. Run `start_app.bat`. You should see:
   `Uvicorn running on http://0.0.0.0:8006`

### 3. Find the Server's IP Address
On the Server PC:
1. Open the Command Prompt (`cmd`).
2. Type `ipconfig` and press Enter.
3. Look for **IPv4 Address** (e.g., `192.168.1.15`).

### 4. Others Access the App
Other people on the **same network (Wi-Fi/LAN)** can now open their browsers and go to:
`http://<SERVER_IP>:8006`
*(Replace `<SERVER_IP>` with the address you found in step 3)*

> [!TIP]
> If it doesn't load, ensure the Server's Firewall allows traffic on Port **8006**.

---

## Scenario 2: Individual Installation
**Every person installs and runs the app on their own PC.**

1. Share the project folder (or GitHub repository) with the user.
2. They must have **Python** installed.
3. They open the folder and run `setup_env.bat`.
4. They create their own `.env` file with an `OPENAI_API_KEY`.
5. They run `start_app.bat`.
6. They access it at `http://localhost:8006`.

---

## Troubleshooting
- **Port 8006 already in use**: If the app fails to start, another program might be using port 8006. You can change the port in `start_app.bat` (at the end of the `uvicorn` line).
- **API Errors**: Ensure the `.env` file has a valid OpenAI key and you have sufficient quota.
- **Firewall**: On Windows, you might need to click "Allow Access" when the Windows Defender Firewall popup appears the first time you run the app.
