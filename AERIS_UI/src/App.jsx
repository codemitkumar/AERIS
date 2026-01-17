import { useState } from "react";
import reactLogo from "./assets/react.svg";
import viteLogo from "/vite.svg";
import "./App.css";
import PilotRoleSelectMode from "./Screens/PilotRoleSelectMode";

function App() {
  const [selectPilotRole, setSelectPilotRole] = useState(null);

  return !selectPilotRole && <PilotRoleSelectMode />;
}

export default App;
