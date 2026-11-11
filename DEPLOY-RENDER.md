# KeyForge on Render

Deploy a Docker web service from this GitHub repository. Select the Free plan and Singapore region. Set KEYFORGE_PASSWORD to a strong private value as a Render environment variable, never commit secrets to GitHub. Optionally set KEYFORGE_USERNAME. The app binds to Render's PORT environment variable. Open /api/health to check health (requires password). Browser profiles are local to each device. This is an experimental geometry exporter: inspect each 3MF in Bambu Studio before printing.
