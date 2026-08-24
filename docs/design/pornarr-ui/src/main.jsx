import React from 'react';
import { createRoot } from 'react-dom/client';
import '@phosphor-icons/web/regular';
import '@phosphor-icons/web/fill';
import './styles/tokens.css';
import './styles/base.css';
import Showcase from './Showcase.jsx';

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <Showcase />
  </React.StrictMode>
);
