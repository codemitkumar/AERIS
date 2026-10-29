import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import SimulateMap from './Screens/SimulateMap.jsx'
import ControlPanel from './Screens/ControlPanel.jsx'

const path = window.location.pathname
const Root = path.startsWith('/simulateMap') ? SimulateMap
  : path.startsWith('/controlPanel') ? ControlPanel
  : App

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
