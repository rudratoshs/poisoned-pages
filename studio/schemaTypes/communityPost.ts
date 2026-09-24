import {defineArrayMember, defineField, defineType} from 'sanity'

// Written by customers on the public forum, not by staff.
// This is exactly the kind of content an attacker can write into.
export const communityPost = defineType({
  name: 'communityPost',
  title: 'Community post',
  type: 'document',
  fields: [
    defineField({name: 'title', type: 'string', validation: (r) => r.required()}),
    defineField({name: 'author', type: 'string', description: 'Forum username'}),
    defineField({name: 'product', type: 'reference', to: [{type: 'product'}]}),
    defineField({name: 'acceptedAnswer', type: 'boolean', initialValue: false}),
    defineField({name: 'body', type: 'array', of: [defineArrayMember({type: 'block'})]}),
  ],
})
