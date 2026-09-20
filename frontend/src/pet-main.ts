/** L10：宠物窗口入口（pet.html）。独立于主应用，只挂 PetView。 */
import { createApp } from 'vue'

import PetView from './components/PetView.vue'

createApp(PetView).mount('#pet')
