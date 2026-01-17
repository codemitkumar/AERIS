import React from "react";
import "./PilotRoleSelectMode.css";
import PilotROleSelectModeHeader from "../Components/PilotROleSelectModeHeader";
import PilotRoleSelectModelBody from "../Components/PilotRoleSelectModelBody";
import PilotRoleSelectModelFooter from "../Components/PilotRoleSelectModelFooter";
function PilotRoleSelectMode() {
  return (
    <div className="pilotRoleSelectModeScreenContainer">
              <PilotROleSelectModeHeader />
              <PilotRoleSelectModelBody />
              <PilotRoleSelectModelFooter/>
    </div>
  );
}

export default PilotRoleSelectMode;
