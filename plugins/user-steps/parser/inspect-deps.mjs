import {defaultRemarkPlugins} from 'streamdown'
console.log(Object.entries(defaultRemarkPlugins).map(([key,value])=>[key,Array.isArray(value)?[value[0].name,value[1]]:value.name]))
