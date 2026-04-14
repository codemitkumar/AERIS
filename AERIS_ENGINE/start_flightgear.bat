@echo off
echo Starting FlightGear (external FDM mode - JSBSim will drive it)...
"C:\Program Files\FlightGear 2024.1\bin\fgfs.exe" ^
  --fg-root="C:\Users\autho\Downloads\Compressed\FlightGear-2024.1.4-data\fgdata_2024_1" ^
  --aircraft=c172p ^
  --airport=VIDP ^
  --runway=28 ^
  --fdm=null ^
  --native-fdm=socket,in,60,127.0.0.1,5500,udp
