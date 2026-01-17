import { Plane } from "lucide-react";
import React from "react";

function PilotROleSelectModeHeader() {
  return (
    <div className="pilotRoleSelectModeScreenHeaderContainer">
      <div className="pilotRoleSelectModeScreenHeaderLeft">
        <div className="pilotRoleSelectModeScreenHeaderLeftIcon">
          <Plane />
        </div>
        <div className="pilotRoleSelectModeScreenHeaderLeftText">
          <span className="pilotRoleSelectModeScreenHeaderLeftTextTop">
            AERIS
          </span>
          <span className="pilotRoleSelectModeScreenHeaderLeftTextBottom">
            Pre-Flight Configuration
          </span>
        </div>
      </div>
      <div className="pilotRoleSelectModeScreenHeaderRight">
        <div className="pilotRoleSelectModeScreenHeaderRightFlight">
          <span className="pilotRoleSelectModeScreenHeaderRightFlightTop">
            Flight
          </span>
          <span className="pilotRoleSelectModeScreenHeaderRightFlightBottom">
            BA-2847
          </span>
        </div>
        <div className="pilotRoleSelectModeScreenHeaderRightAircraft">
          <span className="pilotRoleSelectModeScreenHeaderRightAircraftTop">
            Aircraft
          </span>
          <span className="pilotRoleSelectModeScreenHeaderRightAircraftBottom">
            A320-251N
          </span>
        </div>
      </div>
    </div>
  );
}

export default PilotROleSelectModeHeader;
