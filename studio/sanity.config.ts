import {defineConfig} from 'sanity'
import {structureTool} from 'sanity/structure'
import {visionTool} from '@sanity/vision'
import {schemaTypes} from './schemaTypes'

export default defineConfig({
  name: 'default',
  title: 'Brightside Bikes Help Center',
  projectId: 'ggoz5yx2',
  dataset: 'production',
  plugins: [structureTool(), visionTool()],
  schema: {types: schemaTypes},
})
