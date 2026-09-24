import {defineArrayMember, defineField, defineType} from 'sanity'

export const helpArticle = defineType({
  name: 'helpArticle',
  title: 'Help article',
  type: 'document',
  fields: [
    defineField({name: 'title', type: 'string', validation: (r) => r.required()}),
    defineField({name: 'slug', type: 'slug', options: {source: 'title'}}),
    defineField({
      name: 'topic',
      type: 'string',
      options: {list: ['orders', 'shipping', 'returns', 'warranty', 'battery', 'riding', 'account']},
    }),
    defineField({
      name: 'products',
      title: 'Related products',
      type: 'array',
      of: [defineArrayMember({type: 'reference', to: [{type: 'product'}]})],
    }),
    defineField({name: 'body', type: 'array', of: [defineArrayMember({type: 'block'})]}),
  ],
})
