import React from "react";
import moment from "moment";
function PilotRoleSelectModelFooter() {
  return (
    <div className="pilotRoleSelectModelFooterContainer">
      {moment().format("h:mm a")}
    </div>
  );
}

export default PilotRoleSelectModelFooter;
