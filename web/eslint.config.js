import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

export default [
  { ignores: ['dist'] },
  { files: ['**/*.{ts,tsx}'], languageOptions: { parser: tseslint.parser, ecmaVersion: 2022, globals: globals.browser }, plugins: { 'react-hooks': reactHooks, 'react-refresh': reactRefresh }, rules: { ...js.configs.recommended.rules, ...reactHooks.configs.recommended.rules, ...reactRefresh.configs.vite.rules, 'no-unused-vars': 'off', 'no-undef': 'off', 'react-hooks/set-state-in-effect': 'off', 'react-refresh/only-export-components': 'off' } },
]
