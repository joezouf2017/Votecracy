import js from '@eslint/js'
import react from 'eslint-plugin-react'
import reactHooks from 'eslint-plugin-react-hooks'
import globals from 'globals'
import prettier from 'eslint-config-prettier'

export default [
  { ignores: ['dist/**', 'node_modules/**'] },

  js.configs.recommended,

  {
    files: ['**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: { ...globals.browser },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    settings: { react: { version: 'detect' } },
    plugins: { react, 'react-hooks': reactHooks },
    rules: {
      ...react.configs.flat.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      // The new JSX transform is on via @vitejs/plugin-react, so React does
      // not need to be in scope and importing it would be dead weight.
      'react/react-in-jsx-scope': 'off',
      // No prop-types in this codebase; the components are small and internal.
      'react/prop-types': 'off',
    },
  },

  {
    // Vitest injects describe/it/expect as globals (`globals: true` in
    // vite.config.js), so the test files legitimately use undeclared names.
    files: ['**/*.test.{js,jsx}', 'src/test/**'],
    languageOptions: { globals: { ...globals.node, ...globals.vitest } },
  },

  // Must stay last: turns off every rule that would fight the formatter.
  prettier,
]
