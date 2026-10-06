-- macOS .app entry: read project path from bundle Resources, then start PyQt admin.
on run
	set bundlePath to POSIX path of (path to me)
	if bundlePath ends with "/" then
		set bundlePath to text 1 thru -2 of bundlePath
	end if
	set rootFile to bundlePath & "/Contents/Resources/project-root.txt"
	try
		set projectRoot to do shell script "tr -d '\\r\\n' < " & quoted form of rootFile
	on error
		display alert "WeJam 闲聊控制台" message "App 配置缺失，请在项目目录重新运行 ./scripts/install-desktop-app.sh"
		return
	end try
	set launcher to projectRoot & "/scripts/admin-launcher.sh"
	try
		do shell script "/bin/bash " & quoted form of launcher without altering line endings
	on error errMsg number errNum
		display alert "WeJam 闲聊控制台" message ("启动失败 (" & errNum & "): " & errMsg)
	end try
end run
