import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { AudioPlaybackProvider } from './components/AudioPlayer'
import { ToastProvider } from './components/Feedback'
import { GenerationProvider } from './context/GenerationContext'
import './styles.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <ToastProvider><GenerationProvider><AudioPlaybackProvider><App /></AudioPlaybackProvider></GenerationProvider></ToastProvider>
    </BrowserRouter>
  </React.StrictMode>,
)
