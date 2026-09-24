import {defineField, defineType} from 'sanity'

export const product = defineType({
  name: 'product',
  title: 'Product',
  type: 'document',
  fields: [
    defineField({name: 'name', type: 'string', validation: (r) => r.required()}),
    defineField({name: 'slug', type: 'slug', options: {source: 'name'}}),
    defineField({
      name: 'category',
      type: 'string',
      options: {list: ['e-bike', 'battery', 'accessory', 'service']},
    }),
    defineField({name: 'price', type: 'number', description: 'Price in USD'}),
    defineField({name: 'warrantyYears', type: 'number'}),
    defineField({name: 'rangeMiles', type: 'number', description: 'E-bikes and batteries only'}),
    defineField({name: 'description', type: 'text', rows: 4}),
  ],
})
