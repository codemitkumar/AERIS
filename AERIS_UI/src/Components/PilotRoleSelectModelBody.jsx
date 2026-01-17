import { BadgeQuestionMarkIcon, LaptopMinimal, Plane } from "lucide-react";
import "../Screens/PilotRoleSelectMode.css";
function PilotRoleSelectModelBody() {
  return (
    <div className="pilotRoleSelectModalBodyContainer">
      <div className="pilotRoleSelectModalBodyStaticUiContainer">
        <div className="pilotRoleSelectModalBodyStaticUiIcon">
          <BadgeQuestionMarkIcon />
        </div>
        <div className="pilotRoleSelectModalBodyStaticUiText">
          <span className="pilotRoleSelectModalBodyStaticUiTextTop">
            Select Pilot Role
          </span>
          <span className="pilotRoleSelectModalBodyStaticUiTextBottom">
            Please select your pilot role to proceed with the pre-flight
            configuration.
          </span>
        </div>
      </div>
      <div className="pilotRoleSelectModalBodySelectButtonContainer">
        <div className="pilotRoleSelectModalBodySelectButton captainButton">
          <div className="pilotRoleSelectModalBodySelectButtonIcon ">
            <Plane />
          </div>
          <div className="pilotRoleSelectModalBodySelectButtonText">
            Pilot Flying
          </div>
        </div>
        <div className="pilotRoleSelectModalBodySelectButton firstOfficerButton">
          <div className="pilotRoleSelectModalBodySelectButtonIcon">
            <LaptopMinimal />
          </div>
          <div className="pilotRoleSelectModalBodySelectButtonText">
            Pilot Monitoring
          </div>
        </div>
      </div>
    </div>
  );
}

export default PilotRoleSelectModelBody;
