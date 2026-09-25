{
  "name": "flamin-product",
  "private": true,
  "type": "module",
  "scripts": {
    "build": "tsc --noEmit -p .",
    "test": "node --test \"test/**/*.test.ts\""
  },
  "dependencies": {
    "express": "^5.1.0"
  },
  "devDependencies": {
    "@types/express": "^5.0.0",
    "@types/node": "^22.0.0",
    "typescript": "^5.8.0"
  }
}
