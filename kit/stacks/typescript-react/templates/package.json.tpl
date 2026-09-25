{
  "name": "flamin-web",
  "private": true,
  "type": "module",
  "scripts": {
    "build": "tsc --noEmit -p .",
    "test": "node --test \"test/**/*.test.ts\""
  },
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0"
  },
  "devDependencies": {
    "@types/node": "^22.0.0",
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "typescript": "^5.8.0"
  }
}
