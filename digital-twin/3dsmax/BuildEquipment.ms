-- Run only in an empty scene, or through 3dsmaxbatch.exe.
-- All geometry is created by the installed 3ds Max runtime.
local entry = (getFilenamePath (getSourceFileName())) + "build_equipment.py"
if not doesFileExist entry then throw "Missing build_equipment.py"
python.ExecuteFile entry
