import React from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App.jsx';
import emptyStudy from './fixtures/empty-study.json';
import progressive from './fixtures/progressive.json';
import social from './fixtures/social.json';
import compare from './fixtures/compare.json';
import './styles.css';

function readData() {
  const holder = document.getElementById('wb-data');
  const text = holder?.textContent?.trim();
  if (text && text !== 'null') {
    try {
      return JSON.parse(text);
    } catch (error) {
      console.error('工作台资料无法解析', error);
    }
  }
  const fixture = new URLSearchParams(window.location.search).get('fixture');
  if (fixture === 'progressive') return progressive;
  if (fixture === 'social') return social;
  if (fixture === 'compare') return compare;
  return emptyStudy;
}

createRoot(document.getElementById('root')).render(<App raw={readData()} />);
