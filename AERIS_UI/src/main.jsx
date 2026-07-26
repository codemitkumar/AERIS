import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import SimulateMap from './Screens/SimulateMap.jsx'

const Root = window.location.pathname.startsWith('/simulateMap') ? SimulateMap : App

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
