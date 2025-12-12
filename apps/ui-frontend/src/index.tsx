import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import './index.css';
import App from './App';
import MockGallery from './mock/MockGallery';
import reportWebVitals from './reportWebVitals';

const root = ReactDOM.createRoot(
  document.getElementById('root') as HTMLElement
);
const searchParams = new URLSearchParams(window.location.search);
const mockMode = searchParams.get('mock') === '1';

// basenameを動的に設定
const getRouterBasename = (): string => {
  // GitHub PagesのURL（youtube_production2）かを判定
  const currentOrigin = window.location.origin;
  const currentPath = window.location.pathname;
  const isGitHubPages = currentOrigin.includes('github.io') && currentPath.startsWith('/youtube_production2');

  return isGitHubPages ? '/youtube_production2' : '/';
};

root.render(
  <React.StrictMode>
    {mockMode ? (
      <MockGallery />
    ) : (
      <BrowserRouter basename={getRouterBasename()}>
        <App />
      </BrowserRouter>
    )}
  </React.StrictMode>
);

// If you want to start measuring performance in your app, pass a function
// to log results (for example: reportWebVitals(console.log))
// or send to an analytics endpoint. Learn more: https://bit.ly/CRA-vitals
reportWebVitals();
